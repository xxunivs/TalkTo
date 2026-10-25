"""'더 알려 주실 기억' — 대화 상대가 몰랐던 것을 대화가 끝난 뒤 가족에게 묻는다.

신호(하나만 있어도 후보가 된다. 대답은 지금처럼 자연스럽게 나간다)
- model: 같은 대답 호출에서 AI 가 기억 빈틈을 표시했다(flagGaps → memoryGap {topic}).
  따로 부르지 않는다.
- low_recall: 기억 검색의 가장 높은 유사도가 기준(0.18) 아래이고, 가족의 말이 그분
  이야기를 묻는 말이다(asks_about_memory — 인사·안부 같은 말은 빼는 값싼 규칙).
- feedback: 가족이 대답을 '내용이 틀려요'라고 했다(app/services/corrections.py).

질문 만들기는 대답을 돌려준 뒤 뒤 작업(after_turn — FastAPI BackgroundTasks)이 AI 서버
/v1/persona/gap-question 을 불러 한다. 대화 중에는 질문을 띄우지 않는다(대답 응답에도
싣지 않는다). 화면은 대화를 나갈 때 하나, 대화 상대 화면에서 목록으로 보여 준다.
알림(푸시)은 없다.

규칙
- 대화 상대마다 열린 질문은 MAX_OPEN(10)개까지. 다 차면 새 후보는 버린다(AI 서버도
  부르지 않는다).
- 만든 분(주인)은 열린 질문 모두에 답하거나 '잘 모르겠어요'(dismiss)·'가족에게
  물어보기'(ask-family)를 한다. 답은 바로 기억(source=GAP)으로 넣고 그 자리에서
  임베딩한다(다시 만들기 없음).
- 함께 쓰는 가족은 '가족에게 물어보기'한 질문만 본다. 가족의 답은 pending_owner 로 두고,
  만든 분이 확인(approve)해야 기억이 된다. 빼면(reject) 질문이 다시 열린다(가족의 답은
  지운다). 가족이 답을 기다리게 한 질문도 열린 질문 상한에 센다(빼서 다시 열려도 넘지 않게).
- 답했거나 '잘 모르겠어요'로 닫은 주제는 다시 묻지 않는다(같은 주제 후보는 버리고, AI 서버에도
  닫힌 질문을 함께 보내 합치기·건너뛰기로 정하게 한다).
- 죽음·장례·위기 말(AI 서버 precheck SAFETY 와 같은 규칙)이 나온 대화는 후보로 삼지 않는다.
- 함께 쓰는 가족과의 대화는 후보로 삼지 않는다(대화 내용이 만든 분께 보이지 않게 —
  app/api/v1/conversations.py exchange).
- 기억 제목에 누가 알려 줬는지 적는다: 만든 분이 답하면 만든 분 이름(가족과의 대화에서 그 가족의
  일처럼 말하지 않게), 함께 쓰는 가족의 답은 '가족'(다른 가족에게 누가 함께 쓰는지 드러내지 않게).
- 가족에게 보이는 질문 글의 그분 호칭은 그 가족이 부르는 말로 바꿔 보인다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Feedback,
    GapQuestion,
    Memory,
    Message,
    Person,
    Persona,
    PersonaShare,
    User,
)
from app.db.session import SessionLocal
from app.services import intake, sharing
from app.services.ai import AIServerError, AIServerNotConfigured, ai_service
from app.services.memory_search import MIN_SIMILARITY

logger = logging.getLogger(__name__)

MAX_OPEN = 10
# AI 서버에 '이미 물은 것'으로 함께 보내는 닫힌 질문 수(최근 것부터. 열린 것과 합쳐 20개 안 —
# AI GapQuestionRequest.open_questions 상한)
CLOSED_SENT = 10
# 가족이 적거나 말한 답 한 개(3분 말하기를 글로 옮겨도 들어간다)
ANSWER_MAX = 2000
SIGNALS = ("model", "low_recall", "feedback")
OPEN, ANSWERED, DISMISSED = "open", "answered", "dismissed"
APPLIED, PENDING, REJECTED = "applied", "pending_owner", "rejected"
# 답으로 만든 기억(Memory.source)과 제목 앞머리
MEMORY_SOURCE = "GAP"
MEMORY_LEAD = "가족이 알려 준 이야기"
MEMORY_TAG = "더 알려 주신 기억"

NOT_FOUND = "질문을 찾지 못했어요. 화면을 새로고침해 주세요."
ANSWERED_ALREADY = "이미 답한 질문이에요. 화면을 새로고침해 주세요."
EMBED_FAILED = "기억을 정리하지 못했어요. 잠시 뒤 다시 해 주세요."

# 묻는 말: 물음표·의문사·묻는 끝말
_ASK = re.compile(
    r"[?？]|뭐|뭘|무슨|무엇|어디|언제|누구|누가|몇|어떻게|어땠|왜|얼마|어느|기억\s?(?:나|해|하)"
)
_ASK_END = re.compile(r"(?:니|냐|나요|까|까요|셨어요|셨나요|던가요|었나요|았나요)\s*[.!~]*\s*$")
# 그분 이야기를 묻는 말이 아닌 것: 인사·안부·마음·근황(기억이 비어 보여도 빈틈이 아니다)
_SMALL_TALK = re.compile(
    r"안녕|잘\s?(?:지내|계시|계세|계셨|있|자)|밥\s?(?:은|잘|좀)?\s?(?:먹|드셨|드세)|식사"
    r"|뭐\s?(?:해|하(?:세요|셔|시|니|냐|고))|아프(?:지|신|세|니)|괜찮|건강|보고\s?싶|그리워|사랑"
    r"|고마|감사|미안|죄송|날씨|여보세요|오랜만|반가|(?:거기|하늘)\S{0,3}\s?(?:어때|어떠|괜찮|편안|좋)"
)
_CONTENT = re.compile(r"[\s\W_]+")
# 죽음·장례·위기 말. AI 레포 orchestrator/precheck.py SAFETY 와 같게 둔다(그 대화는 안전 대답
# 길로 가고, 빈틈 질문의 재료로 삼지 않는다).
SAFETY = re.compile(
    r"죽고\s?싶|죽을\s?것\s?같|죽어\s?버리|사라지고\s?싶|없어지고\s?싶|살기\s?싫|살고\s?싶지\s?않"
    r"|자살|자해|목숨|돌아가셨|돌아가신|임종|장례|세상을\s?떠|마지막\s?(?:순간|모습)"
)


def is_safety_turn(text: str) -> bool:
    return bool(SAFETY.search(text or ""))


def asks_about_memory(text: str) -> bool:
    """그분 이야기를 묻는 말인가(값싼 규칙). "안녕?"·"밥 먹었어?"·"거기 어때요?" 는 아니다."""
    t = (text or "").strip()
    if len(_CONTENT.sub("", t)) < 5:
        return False
    if not (_ASK.search(t) or _ASK_END.search(t)):
        return False
    return not _SMALL_TALK.search(t)


def signal_for(
    memory_gap: str | None, top_score: float | None, user_message: str
) -> tuple[str, str] | None:
    """(신호, 주제) 또는 None. 모델 표시가 먼저, 없으면 기억 검색이 빈 묻는 말.
    죽음·장례·위기 말이 나온 대화는 후보가 아니다."""
    if is_safety_turn(user_message):
        return None
    if memory_gap is not None:
        return "model", memory_gap.strip()[:60]
    if top_score is not None and top_score < MIN_SIMILARITY and asks_about_memory(user_message):
        return "low_recall", ""
    return None


def _norm(topic: str | None) -> str:
    return _CONTENT.sub("", topic or "").lower()


# ────────────────────────── 읽기 ──────────────────────────


async def open_count(db: AsyncSession, persona_id: str) -> int:
    return int(
        await db.scalar(
            select(func.count(GapQuestion.id)).where(
                GapQuestion.persona_id == persona_id, GapQuestion.status == OPEN
            )
        )
        or 0
    )


def _visible_to_member(row: GapQuestion, user_id: str) -> bool:
    return row.status == OPEN and row.ask_family and user_id not in (row.skipped_by or [])


async def member_count(db: AsyncSession, persona_id: str, user_id: str) -> int:
    rows = await _rows(db, persona_id, ask_family=True)
    return sum(1 for row in rows if _visible_to_member(row, user_id))


async def badge_count(db: AsyncSession, persona_id: str, user_id: str, *, member: bool) -> int:
    """대화 상대 상세의 gapCount: 만든 분은 열린 질문 수, 가족은 물어봐 달라고 한 열린 질문 수."""
    if member:
        return await member_count(db, persona_id, user_id)
    return await open_count(db, persona_id)


async def waiting_count(db: AsyncSession, persona_id: str) -> int:
    """대화 상대 상세의 gapWaiting(만든 분만): 확인을 기다리는 가족 답과 가족이 고쳐 보낸 대답 수.

    목록(owner_lists pendingAnswers + corrections.pending)과 같은 것을 센다. 열린 질문이 없어도
    가족이 보낸 것이 기다리면 만든 분이 배지로 알 수 있게."""
    answers = await db.scalar(
        select(func.count(GapQuestion.id)).where(
            GapQuestion.persona_id == persona_id,
            GapQuestion.status == ANSWERED,
            GapQuestion.answer_state == PENDING,
        )
    )
    fixes = await db.scalar(
        select(func.count(Feedback.id))
        .join(Message, Message.id == Feedback.message_id)
        .where(Feedback.persona_id == persona_id, Feedback.state == PENDING)
    )
    return int(answers or 0) + int(fixes or 0)


async def _rows(
    db: AsyncSession, persona_id: str, *, ask_family: bool = False
) -> list[GapQuestion]:
    query = select(GapQuestion).where(GapQuestion.persona_id == persona_id)
    if ask_family:
        query = query.where(GapQuestion.ask_family.is_(True))
    return list((await db.scalars(query.order_by(GapQuestion.created_at.desc()))).all())


async def member_names(db: AsyncSession, persona_id: str) -> dict[str, dict]:
    """이 대화 상대에 들어온 가족: 사용자 id → {name, relation}(가족이 적은 내 이름·관계)."""
    shares = (
        await db.scalars(
            select(PersonaShare).where(
                PersonaShare.persona_id == persona_id, PersonaShare.user_id.is_not(None)
            )
        )
    ).all()
    return {
        share.user_id: {
            "name": (share.profile or {}).get("myName"),
            "relation": (share.profile or {}).get("myRelation"),
        }
        for share in shares
    }


def relabel(question: str, labels: tuple[str, str] | None) -> str:
    """질문 글의 그분 호칭(만든 분이 부르는 말)을 가족이 부르는 말로 바꾼다(처음 한 곳).

    질문은 만든 분의 호칭으로 만든다(예: '엄마께서 …'). 손주에게는 '할머니께서 …'로 보여야
    한다. '께서'·'께'는 받침과 상관없어 낱말만 바꿔도 된다."""
    if not labels:
        return question
    owner_term, member_term = labels
    if not owner_term or owner_term == member_term:
        return question
    return question.replace(owner_term, member_term, 1)


async def member_labels(
    db: AsyncSession, persona_id: str, user_id: str
) -> tuple[str, str] | None:
    """(만든 분이 부르는 그분 호칭, 이 가족이 부르는 그분 호칭 — 없으면 '그분')."""
    persona = await db.get(Persona, persona_id)
    if persona is None:
        return None
    person = await db.get(Person, persona.person_id)
    owner_term = (
        (persona.basics or {}).get("addressTerm") or (person.address_term if person else "") or ""
    ).strip()
    share = await db.scalar(
        select(PersonaShare).where(
            PersonaShare.persona_id == persona_id, PersonaShare.user_id == user_id
        )
    )
    member_term = str(((share.profile or {}) if share else {}).get("addressTerm") or "").strip()
    return owner_term, member_term or "그분"


def view(
    row: GapQuestion,
    user_id: str,
    names: dict[str, dict] | None = None,
    labels: tuple[str, str] | None = None,
) -> dict:
    """GapQuestion 한 줄. names 를 주면(만든 분) 가족 답에 누가 답했는지 붙인다.
    labels 를 주면(가족) 질문 글의 그분 호칭을 그 가족이 부르는 말로 바꿔 보인다."""
    data = {
        "id": row.id,
        "personaId": row.persona_id,
        "topic": row.topic,
        "question": relabel(row.question, labels),
        "status": row.status,
        "signal": row.signal,
        "askFamily": bool(row.ask_family),
        "answerState": row.answer_state,
        "answeredByMe": bool(row.answered_by_user_id and row.answered_by_user_id == user_id),
        "answerText": None,
        "answeredBy": None,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
        "answeredAt": row.answered_at,
    }
    if names is not None:
        data["answerText"] = row.answer_text
        if row.answered_by_user_id in names:
            data["answeredBy"] = names[row.answered_by_user_id]
    elif data["answeredByMe"]:
        data["answerText"] = row.answer_text
    return data


async def owner_lists(db: AsyncSession, persona_id: str, user_id: str) -> dict:
    """만든 분: 열린 질문(최근 것부터)과 가족 답(확인 기다림)."""
    rows = await _rows(db, persona_id)
    names = await member_names(db, persona_id)
    return {
        "questions": [view(r, user_id, names) for r in rows if r.status == OPEN],
        "pendingAnswers": [
            view(r, user_id, names)
            for r in rows
            if r.status == ANSWERED and r.answer_state == PENDING
        ],
    }


async def member_lists(db: AsyncSession, persona_id: str, user_id: str) -> dict:
    """함께 쓰는 가족: 물어봐 달라고 한 열린 질문과, 내가 답해 확인을 기다리는 것.
    질문 글의 그분 호칭은 이 가족이 부르는 말로 바꿔 보인다."""
    rows = await _rows(db, persona_id, ask_family=True)
    labels = await member_labels(db, persona_id, user_id)
    return {
        "questions": [
            view(r, user_id, labels=labels) for r in rows if _visible_to_member(r, user_id)
        ],
        "pendingAnswers": [
            view(r, user_id, labels=labels)
            for r in rows
            if r.status == ANSWERED
            and r.answer_state == PENDING
            and r.answered_by_user_id == user_id
        ],
    }


async def get_question(db: AsyncSession, persona_id: str, question_id: str) -> GapQuestion:
    row = await db.get(GapQuestion, question_id)
    if row is None or row.persona_id != persona_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return row


# ────────────────────────── 기억 넣기 ──────────────────────────


async def owner_id(db: AsyncSession, persona: Persona) -> str:
    return await db.scalar(select(Person.owner_user_id).where(Person.id == persona.person_id))


async def save_memory(
    db: AsyncSession,
    persona: Persona,
    *,
    body: str,
    title: str,
    source: str,
    tags: list[str],
    memory_id: str | None = None,
) -> Memory:
    """기억 하나를 넣고(같은 id 가 있으면 고치고) 바로 임베딩한다. commit 은 부르는 쪽이 한다.

    AI 서버가 없으면(개발·시험) 벡터 없이 PENDING 으로 둔다. AI 서버가 실패하면 503 — 벡터
    없는 기억은 대화에서 찾히지 않으니 넣지 않고 다시 하게 한다.
    """
    memory_id = memory_id or str(uuid4())
    vector = None
    try:
        vectors = await ai_service.embed(
            job_id=str(uuid4()),
            items=[{"memorySegmentId": memory_id, "embeddingIndex": 0, "text": body}],
        )
        vector = vectors.get(memory_id)
        if not vector:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, EMBED_FAILED)
    except AIServerNotConfigured:
        vector = None
    except AIServerError as error:
        logger.warning("memory embedding failed: %s", error)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, EMBED_FAILED) from error
    owner = await owner_id(db, persona)
    memory = await db.get(Memory, memory_id)
    if memory is None:
        memory = Memory(id=memory_id, person_id=persona.person_id, created_by_user_id=owner)
        db.add(memory)
    memory.created_by_user_id = owner
    memory.title = title[:120]
    memory.memory_type = "EPISODE"
    memory.related_people = []
    memory.body_markdown = body
    memory.tags = tags[:10]
    memory.status = "ACTIVE"
    memory.source = source
    memory.embedding = vector
    memory.embedding_status = "READY" if vector else "PENDING"
    return memory


def _memory_body(row: GapQuestion, answer: str) -> str:
    topic = (row.topic or "").strip()
    return f"{topic}: {answer}" if topic else answer


async def author_title(
    db: AsyncSession, persona: Persona, author_id: str | None, verb: str, fallback: str
) -> str:
    """기억 제목 앞머리: 만든 분이 적었으면 '{만든 분 이름}이 {verb}', 아니면 fallback.

    질문 답·바로잡은 말은 적은 사람의 일('나 재울 때 …')이라 누가 적었는지 붙인다. 그래야 다른
    가족과의 대화에서 그 가족의 일처럼 말하지 않고, 만든 분과의 대화에서는 만든 분의 일로 안다.
    만든 분 이름은 가족에게도 이미 보인다(sharing.owner_display — 질문 답에 붙이는 '적어 준
    이야기'와 같은 이름). 함께 쓰는 가족이 적은 것은 이름을 붙이지 않는다(다른 가족에게 누가 함께
    쓰는지 드러내지 않게 — fallback '가족이 …')."""
    owner = await owner_id(db, persona)
    if not author_id or author_id != owner:
        return fallback
    user = await db.get(User, owner)
    name = sharing.owner_display(persona, user)
    return f"{name}{sharing.josa_ga(name)} {verb}"


async def _apply_answer(
    db: AsyncSession, persona: Persona, row: GapQuestion, answer: str, author_id: str | None
) -> None:
    topic = (row.topic or "").strip()
    lead = await author_title(db, persona, author_id, "알려 준 이야기", MEMORY_LEAD)
    title = f"{lead} · {topic}" if topic else lead
    memory = await save_memory(
        db,
        persona,
        body=_memory_body(row, answer),
        title=title,
        source=MEMORY_SOURCE,
        tags=[MEMORY_TAG, topic] if topic else [MEMORY_TAG],
        memory_id=row.memory_id,
    )
    row.memory_id = memory.id
    row.answer_state = APPLIED


def clean_answer(text: str | None) -> str:
    answer = str(text or "").strip()
    if not answer:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "답을 적거나 말씀해 주세요.")
    if len(answer) > ANSWER_MAX:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"답은 {ANSWER_MAX}자까지예요. 나눠서 적어 주세요.",
        )
    return answer


# ────────────────────────── 만든 분·가족의 일 ──────────────────────────


async def answer(
    db: AsyncSession, persona: Persona, row: GapQuestion, user_id: str, text: str, *, member: bool
) -> GapQuestion:
    """답하기. 만든 분은 바로 기억이 되고, 가족은 만든 분 확인을 기다린다. commit 한다."""
    answer_text = clean_answer(text)
    now = datetime.now(UTC)
    if member:
        if not _visible_to_member(row, user_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        row.status = ANSWERED
        row.answer_state = PENDING
    else:
        # 만든 분은 열린 질문과, 가족 답이 확인을 기다리는 질문(만든 분 답으로 바꾼다)에 답한다
        if not (row.status == OPEN or row.answer_state == PENDING):
            raise HTTPException(status.HTTP_409_CONFLICT, ANSWERED_ALREADY)
        await _apply_answer(db, persona, row, answer_text, user_id)
        row.status = ANSWERED
        row.decided_at = now
    row.answer_text = answer_text
    row.answered_by_user_id = user_id
    row.answered_at = now
    await db.commit()
    return row


async def dismiss(db: AsyncSession, row: GapQuestion, user_id: str, *, member: bool) -> GapQuestion:
    """'잘 모르겠어요'. 만든 분은 질문을 닫고, 가족은 그 가족에게만 숨긴다. commit 한다."""
    if member:
        if not _visible_to_member(row, user_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        row.skipped_by = [*(row.skipped_by or []), user_id]
    else:
        if row.status != OPEN:
            raise HTTPException(status.HTTP_409_CONFLICT, ANSWERED_ALREADY)
        row.status = DISMISSED
    await db.commit()
    return row


async def ask_family(
    db: AsyncSession, persona: Persona, row: GapQuestion, ask: bool, members: int
) -> GapQuestion:
    """'가족에게 물어보기'(만든 분, 함께 쓰는 가족이 있을 때만). ask=False 면 거둔다.
    commit 한다."""
    if ask and members < 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "함께 쓰는 가족이 아직 없어요. 가족을 초대한 뒤에 물어볼 수 있어요.",
        )
    if row.status != OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, ANSWERED_ALREADY)
    row.ask_family = ask
    await db.commit()
    return row


CHANGED_SINCE_SEEN = "가족 답이 그 사이에 바뀌었어요. 화면을 새로고침해 주세요."


def _utc_ms(value: datetime | None) -> datetime | None:
    """시간대 있는 UTC, 1/1000초까지. 시간대가 없으면 UTC 로 본다(시험 SQLite 는 시간대 없이
    돌려준다). 웹 화면의 시각은 1/1000초까지라 그 아래는 비교하지 않는다."""
    if value is None:
        return None
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def _check_seen(row: GapQuestion, seen: tuple[str | None, datetime | None] | None) -> None:
    """seen 은 만든 분이 목록에서 본 (답 글, 답한 때)다. 지금 답과 다르면 409.

    가족이 나가 질문이 다시 열리고(sharing.end_share) 다른 가족이 답했거나, 다른 기기에서 빼서
    다시 답한 것을 만든 분이 옛 화면에서 확인하면 본 적 없는 답이 기억이 된다. 답한 때도 보아서
    다른 가족이 같은 글로 답한 것도 막는다(기억에 누가 알려 줬는지 붙는다). seen 이 없으면(예전
    화면) 보지 않는다."""
    if seen is None:
        return
    text, answered_at = seen
    same_text = str(text or "").strip() == row.answer_text
    if not same_text or _utc_ms(answered_at) != _utc_ms(row.answered_at):
        raise HTTPException(status.HTTP_409_CONFLICT, CHANGED_SINCE_SEEN)


async def approve(
    db: AsyncSession,
    persona: Persona,
    row: GapQuestion,
    seen: tuple[str | None, datetime | None] | None = None,
) -> GapQuestion:
    """가족 답을 기억에 넣는다(만든 분). seen 은 _check_seen. commit 한다."""
    if row.answer_state != PENDING or not row.answer_text:
        raise HTTPException(status.HTTP_409_CONFLICT, "확인을 기다리는 가족 답이 없어요.")
    _check_seen(row, seen)
    await _apply_answer(db, persona, row, row.answer_text, row.answered_by_user_id)
    row.status = ANSWERED
    row.decided_at = datetime.now(UTC)
    await db.commit()
    return row


async def reject(
    db: AsyncSession, row: GapQuestion, seen: tuple[str | None, datetime | None] | None = None
) -> GapQuestion:
    """가족 답을 넣지 않는다(만든 분). 질문은 다시 열리고 가족의 답은 지운다. seen 은 _check_seen
    (본 적 없는 답을 지우지 않게). commit 한다."""
    if row.answer_state != PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "확인을 기다리는 가족 답이 없어요.")
    _check_seen(row, seen)
    row.status = OPEN
    row.answer_state = REJECTED
    row.answer_text = None
    row.answered_by_user_id = None
    row.answered_at = None
    row.decided_at = datetime.now(UTC)
    await db.commit()
    return row


# ────────────────────────── 대답 뒤 작업 ──────────────────────────

# 같은 대화 상대의 후보를 한 번에 하나씩(열린 질문 상한·합치기가 엇갈리지 않게). 서버 한 대 전제.
_locks: dict[str, asyncio.Lock] = {}


def _lock(persona_id: str) -> asyncio.Lock:
    lock = _locks.get(persona_id)
    if lock is None:
        lock = _locks[persona_id] = asyncio.Lock()
    return lock


async def after_turn(
    *,
    persona_id: str,
    message_id: str | None,
    user_message: str,
    reply: str,
    signal: str,
    topic: str = "",
    correction: str | None = None,
    from_member: bool = False,
) -> None:
    """대답을 돌려준 뒤(뒤 작업) 후보 하나로 질문을 만들거나 합친다. 실패해도 아무 일
    없다(로그만).

    from_member: 함께 쓰는 가족의 대화·고침에서 온 후보다. 질문은 만든 분이 보므로 AI 서버에
    fromMember 를 보내 그 가족이 한 말(이름·사건·사정)을 질문에 옮기지 않게 하고, 주제도 대답
    모델의 표시 주제로 채우지 않는다(약관 family_share: 대화 내용은 빼고 질문만)."""
    if signal not in SIGNALS or not (user_message or "").strip():
        return
    if is_safety_turn(user_message):
        return  # 죽음·장례·위기 말이 나온 대화('내용이 틀려요' 후보 포함)는 묻지 않는다
    try:
        async with _lock(persona_id):
            async with SessionLocal() as db:
                await _make_question(
                    db,
                    persona_id=persona_id,
                    message_id=message_id,
                    user_message=user_message,
                    reply=reply,
                    signal=signal,
                    topic=topic,
                    correction=correction,
                    from_member=from_member,
                )
    except Exception:  # noqa: BLE001 — 뒤 작업이 실패해도 대화는 그대로다
        logger.exception("gap question task failed")


async def _make_question(
    db: AsyncSession,
    *,
    persona_id: str,
    message_id: str | None,
    user_message: str,
    reply: str,
    signal: str,
    topic: str,
    correction: str | None,
    from_member: bool = False,
) -> None:
    persona = await db.get(Persona, persona_id)
    if persona is None or persona.hold_reason:
        # 멈춘 대화 상대는 질문을 만들지 않는다(뒤 작업 사이에 걸린 멈춤 — 해외 회사를 부르지
        # 않는다)
        return
    every = await _rows(db, persona_id)
    if message_id and any(r.source_message_id == message_id for r in every):
        return  # 같은 대답에서 이미 만들었다(평가로 다시 온 후보 등)
    rows = [r for r in every if r.status == OPEN]
    # 답했거나 '잘 모르겠어요'로 닫은 질문(최근 것부터). 그 주제는 다시 묻지 않는다
    # ('이 질문은 더 여쭤보지 않을게요' 약속).
    closed = [r for r in every if r.status in (ANSWERED, DISMISSED)]
    closed_topics = {_norm(r.topic) for r in closed} - {""}
    if topic and _norm(topic) in closed_topics:
        return
    same = next((r for r in rows if topic and _norm(r.topic) == _norm(topic)), None)
    if same is not None:
        same.hits = (same.hits or 1) + 1
        await db.commit()
        return
    # 가족 답이 만든 분 확인을 기다리는 질문도 센다(빼면 다시 열린다 — 상한을 넘지 않게)
    active = [r for r in every if r.status == OPEN or r.answer_state == PENDING]
    if len(active) >= MAX_OPEN:
        return
    person = await db.get(Person, persona.person_id)
    basics = persona.basics or {}
    # 열린 질문과 함께 최근 닫은 질문도 보낸다(같은 것이면 AI 가 합치기를 고르고, 닫힌 것에
    # 합치라고 하면 아래에서 아무것도 하지 않는다 = 다시 묻지 않는다). 열린 것은 상한 아래라
    # 합쳐도 AI 서버 상한(20개) 안이다.
    sent = [*rows, *closed[:CLOSED_SENT]]
    request = {
        "candidate": {
            "userMessage": user_message[:4000],
            "reply": (reply or "")[:4000],
            "topic": (topic or "")[:60],
            "signal": signal,
            **({"correction": correction[:300]} if correction else {}),
        },
        "openQuestions": [
            {"id": r.id, "topic": r.topic or "", "question": r.question} for r in sent
        ],
        "subjectLabel": (basics.get("addressTerm") or person.address_term or "")[:40] or None,
        "subjectSelf": intake.is_self(basics),
        "deceased": _deceased(person),
        # 만든 분 자신의 후보면 칸을 보내지 않는다(예전 AI 서버와도 그대로 맞는다)
        **({"fromMember": True} if from_member else {}),
    }
    # 읽기를 마쳤다. 질문 만들기(LLM)를 기다리는 동안 연결을 트랜잭션 속에 붙잡아 두지 않는다
    # (expire_on_commit=False 라 읽은 줄은 그대로 쓴다. 상한·합치기는 대화 상대별 잠금이 지킨다).
    await db.commit()
    try:
        result = await ai_service.gap_question(**request)
    except AIServerNotConfigured:
        return
    action = result.get("action")
    if action == "merge":
        target = next((r for r in rows if r.id == result.get("mergeInto")), None)
        if target is not None:
            target.hits = (target.hits or 1) + 1
            await db.commit()
        return
    question = " ".join(str(result.get("question") or "").split())[:200]
    if action != "new" or not question:
        return
    # 가족 대화에서 온 후보면 대답 모델의 표시 주제(그 가족이 물은 말에서 나온 것)로 채우지 않는다
    fallback = "" if from_member else topic
    new_topic = (str(result.get("topic") or fallback or "").strip())[:60]
    if _norm(new_topic) and _norm(new_topic) in closed_topics:
        return  # AI 가 고른 주제가 이미 닫은 질문과 같다(다시 묻지 않는다)
    db.add(
        GapQuestion(
            persona_id=persona_id,
            topic=new_topic,
            question=question,
            status=OPEN,
            signal=signal,
            source_message_id=message_id,
        )
    )
    await db.commit()


def _deceased(person: Person | None) -> bool | None:
    """질문 시제용 고인 여부(살아 계심이 분명할 때만 False, 모르면 None — AI 서버는 지난
    일로 묻는다)."""
    life = person.life_status if person is not None else None
    if life == "LIVING":
        return False
    if life == "DECEASED":
        return True
    return None
