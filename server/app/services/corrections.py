"""대화 고치기 — 말풍선 ⋯ 평가(Feedback)를 대화에 반영한다.

고르는 것(Feedback.tags)
- LIKE 그분 같았어요 · STYLE 말투가 달라요 · CONTENT 내용이 틀려요
- TOO_LONG 너무 길어요 · VOICE 목소리가 달라요
- STYLE·CONTENT 에는 "그분이라면 어떻게 말하셨을까요?" 한 줄(correction, 300자)을
  붙일 수 있다(글 또는 🎤).

반영(만든 분은 바로, 함께 쓰는 가족은 만든 분이 확인한 뒤 — 그 전에는 state=pending_owner)
- STYLE + 고침: (예전 대답, 고친 말) 쌍을 StyleExample 로 둔다. 대화마다 최근
  STYLE_LIMIT(10)개를 AI 서버에 styleExamples 로 보내 '말투를 이렇게 고쳐 달라고 했어요'
  예시 절이 된다(다음 대답부터). 글 예시로만 쓰고 목소리로 읽지 않는다(그분 목소리로
  아무 문장이나 읽히지 않는다).
- CONTENT + 고침: 고침을 기억(source=FEEDBACK)으로 넣고 바로 임베딩한다. 그 대답에 쓰인 기억에는
  flagged_wrong=True(끌 후보 표시)만 한다 — 지우거나 숨기지 않는다.
- CONTENT 는 '더 알려 주실 기억' 신호(feedback)도 된다(app/services/gaps.py).
- TOO_LONG·VOICE·LIKE 는 저장만 한다(관리자 목록에서 본다). state=none.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Feedback, Memory, Message, Persona, StyleExample
from app.services import forgetting, gaps

LIKE, STYLE, CONTENT, TOO_LONG, VOICE = "LIKE", "STYLE", "CONTENT", "TOO_LONG", "VOICE"
FEEDBACK_TAGS = (LIKE, STYLE, CONTENT, TOO_LONG, VOICE)
APPLIED, PENDING, REJECTED, NONE = "applied", "pending_owner", "rejected", "none"
CORRECTION_MAX = 300
# 대화마다 AI 서버에 보내는 고친 말투 수(AI PersonaResponseRequest.style_examples 상한)
STYLE_LIMIT = 10
# StyleExample.original 에 남기는 예전 대답 길이(AI StyleExample.original 상한 2,000자 안)
ORIGINAL_MAX = 1000
MEMORY_SOURCE = "FEEDBACK"
MEMORY_TITLE = "가족이 바로잡은 이야기"
MEMORY_TAG = "대화 고침"


def clean_correction(text: str | None) -> str | None:
    value = " ".join(str(text or "").split())
    return value[:CORRECTION_MAX] or None


def wants(tags: list[str], correction: str | None) -> set[str]:
    """반영할 것: STYLE·CONTENT 가운데 고침 한 줄이 함께 온 것."""
    if not correction:
        return set()
    return {tag for tag in (STYLE, CONTENT) if tag in tags}


async def style_examples(
    db: AsyncSession, persona_id: str, *, deceased: bool = False
) -> list[dict[str, str]]:
    """대화에 보낼 고친 말투(최근 STYLE_LIMIT 개, 오래된 것부터). 없으면 빈 목록.

    deceased 면 '기억이 안 난다'·'까먹었다' 같은 말이 든 고친 말은 빼고 보낸다(고인 대화 상대는
    잊었다고 하지 않는다 — 말버릇으로 따라 하지 않게. app/services/forgetting.py)."""
    rows = (
        await db.scalars(
            select(StyleExample)
            .where(StyleExample.persona_id == persona_id)
            .order_by(StyleExample.created_at.desc())
            .limit(STYLE_LIMIT)
        )
    ).all()
    return [
        {"original": row.original[:ORIGINAL_MAX], "corrected": row.corrected}
        for row in reversed(rows)
        if row.original.strip()
        and row.corrected.strip()
        and not (deceased and forgetting.says_forgot(row.corrected))
    ]


async def clear_applied(db: AsyncSession, item: Feedback) -> None:
    """이 평가로 반영했던 것(고친 말투·FEEDBACK 기억)을 거둔다(고쳐 다시 보내거나 뺄 때)."""
    await db.execute(delete(StyleExample).where(StyleExample.feedback_id == item.id))
    if item.memory_id:
        await db.execute(
            delete(Memory).where(Memory.id == item.memory_id, Memory.source == MEMORY_SOURCE)
        )
        item.memory_id = None


async def apply(db: AsyncSession, persona: Persona, item: Feedback, message: Message) -> bool:
    """고침을 반영한다(만든 분이 보냈거나 가족 고침을 확인함). 내용 고침이면 True(빈틈 신호).
    commit 은 부르는 쪽이 한다."""
    todo = wants(item.tags or [], item.correction)
    if STYLE in todo:
        db.add(
            StyleExample(
                persona_id=persona.id,
                feedback_id=item.id,
                original=(message.content or "")[:ORIGINAL_MAX],
                corrected=item.correction,
                created_by_user_id=item.user_id,
            )
        )
    if CONTENT in todo:
        # 만든 분이 바로잡았으면 '{만든 분 이름}이 바로잡은 이야기'(다른 가족과의 대화에서 그
        # 가족의 일처럼 말하지 않게), 함께 쓰는 가족의 것은 '가족이 바로잡은 이야기'
        title = await gaps.author_title(
            db, persona, item.user_id, "바로잡은 이야기", MEMORY_TITLE
        )
        memory = await gaps.save_memory(
            db,
            persona,
            body=item.correction,
            title=title,
            source=MEMORY_SOURCE,
            tags=[MEMORY_TAG],
        )
        item.memory_id = memory.id
        await flag_wrong(db, persona, message, keep=memory.id)
    item.state = APPLIED
    item.applied_at = datetime.now(UTC)
    return CONTENT in todo


async def flag_wrong(
    db: AsyncSession, persona: Persona, message: Message, keep: str | None = None
) -> None:
    """'내용이 틀려요' 대답에 쓰인 기억에 끌 후보 표시만 한다(지우거나 숨기지 않는다)."""
    ids = [i for i in (message.retrieved_memory_ids or []) if i and i != keep]
    if not ids:
        return
    await db.execute(
        update(Memory)
        .where(Memory.id.in_(ids), Memory.person_id == persona.person_id)
        .values(flagged_wrong=True)
    )


async def asked_before(db: AsyncSession, message: Message) -> str:
    """그 대답 바로 앞의 사용자 말(빈틈 질문 재료). 없으면 빈 글."""
    asked = await db.scalar(
        select(Message.content)
        .where(
            Message.conversation_id == message.conversation_id,
            Message.role == "user",
            Message.created_at <= message.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return asked or ""


async def pending(db: AsyncSession, persona_id: str) -> list[dict]:
    """만든 분이 확인할 가족 고침(오래된 것부터)."""
    rows = (
        await db.execute(
            select(Feedback, Message.content)
            .join(Message, Message.id == Feedback.message_id)
            .where(Feedback.persona_id == persona_id, Feedback.state == PENDING)
            .order_by(Feedback.created_at)
        )
    ).all()
    names = await gaps.member_names(db, persona_id)
    return [
        {
            "id": item.id,
            "messageId": item.message_id,
            "tags": item.tags or [],
            "kinds": sorted(wants(item.tags or [], item.correction)),
            "original": content,
            "correction": item.correction,
            "state": item.state,
            "createdAt": item.created_at,
            "answeredBy": names.get(item.user_id),
        }
        for item, content in rows
    ]


CHANGED_SINCE_SEEN = "가족이 고침을 바꿨어요. 목록을 새로 보고 다시 확인해 주세요."


async def pending_item(
    db: AsyncSession,
    persona_id: str,
    feedback_id: str,
    seen: tuple[list[str], str | None] | None = None,
) -> tuple[Feedback, Message]:
    """만든 분이 확인할 가족 고침 하나.

    seen 은 만든 분이 목록에서 본 (고른 것, 고친 말)이다. 가족은 확인을 기다리는 동안에도 고쳐
    다시 보낼 수 있어서(오타 고치기), 본 것과 지금 것이 다르면 409 로 막는다 — 만든 분이 보지 않은
    글이 모든 대화의 말투 예시·기억으로 들어가지 않게. 고른 것은 순서와 상관없이 같은지 본다.
    seen 이 없으면(예전 화면) 보지 않는다."""
    item = await db.get(Feedback, feedback_id)
    message = await db.get(Message, item.message_id) if item is not None else None
    if item is None or message is None or item.persona_id != persona_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "고친 내용을 찾지 못했어요.")
    if item.state != PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "이미 확인한 고침이에요. 화면을 새로고침해 주세요."
        )
    if seen is not None:
        tags, correction = seen
        if set(tags) != set(item.tags or []) or clean_correction(correction) != item.correction:
            raise HTTPException(status.HTTP_409_CONFLICT, CHANGED_SINCE_SEEN)
    return item, message
