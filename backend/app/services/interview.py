"""기억 인터뷰 신청(초기 가족 무료, 대표가 직접 한다).

흐름
- 만든 분(주인)이 대화 상대 화면에서 신청한다(이름·관계·전화번호·바라는 요일과 시간·전화/영상,
  동의 둘). 인터뷰 받는 분은 만든 분이다. 그분(대상자)은 인터뷰하지 않는다. 그래서 '나 자신'
  대화 상대(만든 분이 곧 그분)는 신청할 수 없고, 함께 쓰는 가족도 신청하지 않는다.
- 동의는 대화 상대별 종류로 남긴다: interview_use:<id>(필수) · interview_recording:<id>(선택),
  문서는 interview(app/static/legal/interview.md).
- 새 신청은 디스코드(notify)와 운영 메일(mailer)로 알린다. 둘 다 개인 정보 없이 '1건'만.
- 관리자가 들은 내용을 글로 넣는다: 질문 번호별 답(InterviewEntry 에 남기고 persona.answers 에도
  합친다), 기억 카드(Memory source=INTERVIEW, 바로 임베딩). 그다음 '다시 만들기'(UPDATE 작업)로
  지침·질문 답 기억을 다시 만든다. 다시 만들기는 RECORDING·INTAKE 기억만 바꾸므로 INTERVIEW
  기억은 그대로 남는다(persona_builder._assemble).
- 만든 분이 넣은 이야기를 빼 달라고 하면(interview.md) 관리자가 넣은 답 기록을 지워 그 답이 더한
  부분만 answers 에서 빼고(remove_answer), 기억 카드를 지운 뒤(remove_memory) 다시 만든다.
- 녹음 파일은 받지 않는다(대표가 따로 암호화해 보관한다). 글만 넣는다.
- 끝나거나(done) 취소한(cancelled) 신청의 이름·연락처·희망 일정은 30일 뒤 하루 정리가 지운다.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    ORIGIN_IMPORT,
    InterviewEntry,
    InterviewRequest,
    JobStatus,
    Memory,
    Person,
    Persona,
    SetupStep,
    User,
)
from app.services import analytics, gate, intake, jobs, legal, mailer
from app.services.ai import AIServerNotConfigured, ai_service

logger = logging.getLogger(__name__)

STATUSES = ("requested", "scheduled", "done", "cancelled")
OPEN = ("requested", "scheduled")
CLOSED = ("done", "cancelled")
METHODS = ("phone", "video")
DAYS = ("월", "화", "수", "목", "금", "토", "일")
# 끝나거나 취소한 뒤 이름·연락처·희망 일정을 두는 기간
RETENTION = timedelta(days=30)
USE_TYPE = "interview_use"
RECORDING_TYPE = "interview_recording"
DOC = "interview"
ENTRY_SOURCE = "interview"
MEMORY_SOURCE = "INTERVIEW"
MEMORY_TAG = "기억 인터뷰"
MEMORY_MAX = 2000
NOTE_MAX = 1000
ENTRY_MAX = 2000
# 질문 답으로 받지 않는 문항 종류(가족 표는 정보 고치기 화면에서 칸마다 넣는다)
_TEXT_TYPES = ("text", "textarea")
_LIST_TYPES = ("multi", "phrases")
PHRASES_MAX = 10  # intake.validate_answers 와 같다
ITEM_MAX = 80

SELF_BLOCKED = "내 이야기로 만든 대화 상대는 기억 인터뷰를 신청할 수 없어요."
ALREADY = "이미 신청했어요. 대표가 곧 연락드릴게요."
USE_REQUIRED = "인터뷰 내용을 이 대화 상대에 넣는 데 동의해 주셔야 신청할 수 있어요."
NO_USE_CONSENT = (
    "만든 분이 기억 인터뷰 내용을 넣는 데 동의하지 않았어요(신청이 없거나 동의를 거뒀어요)."
)
NOT_READY = "다 만든 대화 상대에만 넣을 수 있어요."
OPS_SUBJECT = "새 기억 인터뷰 신청 1건"
# 같은 대화 상대의 새 신청 알림(운영 메일)은 이 동안 한 번만(신청·취소를 되풀이해도)
NOTIFY_EVERY = timedelta(hours=24)


class InterviewError(ValueError):
    """화면에 그대로 보여 줄 입력 오류."""


class InterviewConflict(InterviewError):
    """지금 상태로는 할 수 없는 일(이미 신청함·나 자신·취소할 신청 없음). 409."""


class InterviewNotFound(InterviewError):
    """이 대화 상대의 것이 아닌 기록(넣은 답·기억 카드). 404."""


# ────────────────────────── 신청 ──────────────────────────


def blocked_reason(persona: Persona) -> str | None:
    """이 대화 상대로 신청할 수 없는 까닭. 되면 None. 주인인지는 부르는 쪽이 본다."""
    if intake.is_self(persona.basics or {}):
        return SELF_BLOCKED
    return None


def prefill(persona: Persona) -> dict:
    """인터뷰 받을 분(만든 분)의 이름·관계. 만들 때 적은 '내 이름'·'그분과의 관계'."""
    basics = persona.basics or {}
    return {"name": basics.get("myName") or "", "relation": basics.get("myRelation") or ""}


def normalize_phone(raw: str | None) -> str:
    """국내 전화번호만 받는다(0으로 시작하는 9~11자리). 휴대폰은 010-1234-5678 꼴로."""
    digits = re.sub(r"\D", "", raw or "")
    if not re.fullmatch(r"0\d{8,10}", digits):
        raise InterviewError("연락받을 전화번호를 확인해 주세요.")
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return digits


def clean_days(days: list[str] | None) -> list[str]:
    picked = {str(d).strip() for d in days or []}
    out = [d for d in DAYS if d in picked]
    if not out:
        raise InterviewError("인터뷰 받기 편한 요일을 하나 이상 골라 주세요.")
    return out


def _text(value: str | None, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


async def open_request(db: AsyncSession, persona_id: str) -> InterviewRequest | None:
    """이 대화 상대의 아직 끝나지 않은 신청(신청함·일정 잡음). 없으면 None."""
    return await db.scalar(
        select(InterviewRequest)
        .where(InterviewRequest.persona_id == persona_id, InterviewRequest.status.in_(OPEN))
        .order_by(InterviewRequest.created_at.desc())
        .limit(1)
    )


async def latest_request(db: AsyncSession, persona_id: str) -> InterviewRequest | None:
    """주인 화면에 보일 마지막 신청(끝난 것도). 없으면 None."""
    return await db.scalar(
        select(InterviewRequest)
        .where(InterviewRequest.persona_id == persona_id)
        .order_by(InterviewRequest.created_at.desc())
        .limit(1)
    )


def view(req: InterviewRequest, *, contact: bool = True) -> dict:
    """신청 하나. contact=False 면 전화번호를 뺀다(관리자 목록)."""
    out = {
        "id": req.id,
        "personaId": req.persona_id,
        "status": req.status,
        "name": req.name,
        "relation": req.relation,
        "days": req.days or [],
        "timeText": req.time_text,
        "method": req.method,
        "recordingOk": bool(req.recording_ok),
        "createdAt": req.created_at,
        "updatedAt": req.updated_at,
        "closedAt": req.closed_at,
        "wiped": req.wiped_at is not None,
    }
    if contact:
        out["phone"] = req.phone
    return out


async def apply(
    db: AsyncSession,
    user: User,
    persona: Persona,
    *,
    name: str,
    relation: str,
    phone: str,
    days: list[str],
    time_text: str,
    method: str,
    agree_use: bool,
    agree_recording: bool,
) -> InterviewRequest:
    """신청을 받는다(주인만 — 부르는 쪽이 주인인지 본다). commit 하고 알린다."""
    reason = blocked_reason(persona)
    if reason:
        raise InterviewConflict(reason)
    if not agree_use:
        raise InterviewError(USE_REQUIRED)
    clean_name = _text(name, 20)
    if not clean_name:
        raise InterviewError("인터뷰 받으실 분의 이름을 적어 주세요.")
    clean_relation = _text(relation, 20)
    if not clean_relation:
        raise InterviewError("그분과 어떤 사이인지 적어 주세요.")
    if method not in METHODS:
        raise InterviewError("전화와 영상 통화 가운데 골라 주세요.")
    clean_time = _text(time_text, 60)
    if not clean_time:
        raise InterviewError("연락받기 편한 시간을 적어 주세요.")
    request = InterviewRequest(
        persona_id=persona.id,
        owner_user_id=user.id,
        name=clean_name,
        relation=clean_relation,
        phone=normalize_phone(phone),
        days=clean_days(days),
        time_text=clean_time,
        method=method,
        recording_ok=bool(agree_recording),
        status="requested",
    )
    # 같은 대화 상대에 두 번 눌러 둘이 생기지 않게 대화 상대 줄을 잡은 채 보고 넣는다
    await db.execute(select(Persona.id).where(Persona.id == persona.id).with_for_update())
    if await open_request(db, persona.id) is not None:
        raise InterviewConflict(ALREADY)
    # 하루 안에 같은 대화 상대로 신청한 적이 있으면(신청·취소를 되풀이) 알림을 또 보내지 않는다
    repeat = bool(
        await db.scalar(
            select(func.count(InterviewRequest.id)).where(
                InterviewRequest.persona_id == persona.id,
                InterviewRequest.created_at >= datetime.now(UTC) - NOTIFY_EVERY,
            )
        )
    )
    db.add(request)
    await legal.record(
        db, user, legal.persona_type(USE_TYPE, persona.id), True, persona_id=persona.id, doc=DOC
    )
    await legal.record(
        db,
        user,
        legal.persona_type(RECORDING_TYPE, persona.id),
        bool(agree_recording),
        persona_id=persona.id,
        doc=DOC,
    )
    await db.commit()
    # repeat=True 면 통계에만 남기고 디스코드로 알리지 않는다(notify._event_text)
    await analytics.track(
        db,
        "interview_requested",
        user_id=user.id,
        persona_id=persona.id,
        method=method,
        notify_email=user.email,
        **({"repeat": True} if repeat else {}),
    )
    if not repeat:
        notify_ops()
    return request


def notify_ops() -> None:
    """운영 메일함으로 알린다(개인 정보 없이).

    디스코드는 analytics.track → notify.event 가 보낸다."""
    link = f"{get_settings().frontend_url.rstrip('/')}/#/admin/interviews"
    mailer.send_ops(
        OPS_SUBJECT,
        f"{OPS_SUBJECT} — 관리자 화면에서 확인해 주세요.\n{link}\n\n"
        "(이 메일에는 신청하신 분의 정보를 싣지 않아요.)",
    )


async def cancel(db: AsyncSession, user: User, persona: Persona) -> InterviewRequest:
    """주인이 아직 끝나지 않은 신청을 취소한다."""
    request = await open_request(db, persona.id)
    if request is None:
        raise InterviewConflict("취소할 신청이 없어요.")
    set_status(request, "cancelled")
    await db.commit()
    await analytics.track(db, "interview_request_canceled", user_id=user.id, persona_id=persona.id)
    return request


def set_status(request: InterviewRequest, status: str, at: datetime | None = None) -> None:
    """상태를 바꾼다. 끝남·취소면 30일 정리의 기준 시각을 남기고, 다시 열면 비운다.

    끝남 ↔ 취소처럼 닫힌 상태끼리 바꾸면 처음 닫은 시각을 그대로 둔다(30일 정리가 다시 늦춰지지
    않게)."""
    if status not in STATUSES:
        raise InterviewError("모르는 상태예요.")
    if status == request.status:
        return
    if status in CLOSED:
        if not (request.status in CLOSED and request.closed_at):
            request.closed_at = at or datetime.now(UTC)
    else:
        request.closed_at = None
    request.status = status


async def wipe_closed(db: AsyncSession, at: datetime | None = None) -> int:
    """끝나거나 취소한 지 30일이 지난 신청의 이름·연락처·희망 일정을 지운다. 지운 수(commit 함)."""
    current = at or datetime.now(UTC)
    result = await db.execute(
        update(InterviewRequest)
        .where(
            InterviewRequest.status.in_(CLOSED),
            InterviewRequest.closed_at.is_not(None),
            InterviewRequest.closed_at < current - RETENTION,
            InterviewRequest.wiped_at.is_(None),
        )
        .values(name=None, phone=None, days=None, time_text=None, wiped_at=current)
    )
    await db.commit()
    return int(result.rowcount or 0)


# ────────────────────────── 관리자: 들은 내용 넣기 ──────────────────────────


def questions() -> dict[str, dict]:
    """질문 번호 → 질문(만들 때 질문 + 기억 더 채우기 질문)."""
    return {q["id"]: q for q in [*intake.QUESTIONS, *intake.MORE_QUESTIONS]}


def _empty(value) -> bool:
    """답이 없는가. '기억이 안 나요'·'따로 없었어요' 같은 표시 값(__…__)도 빈 답으로 본다."""
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip()
        return not text or (text.startswith("__") and text.endswith("__"))
    if isinstance(value, list):
        return all(_empty(v) for v in value)
    return False


def merge_answer(answers: dict, question: dict, text: str, *, replace: bool = False) -> dict:
    """들은 답 하나를 지금 답에 합친 새 답(검사 전). 글 칸은 덧붙이고(긴 글) 고르는 칸은 바꾼다.

    replace=True 면 지금 답을 버리고 들은 답으로 바꾼다. 넘치면 자르지 않고 오류로 알린다
    (intake.validate_answers 는 말없이 자르니 그 전에 본다).
    """
    qid = question["id"]
    kind = question.get("type")
    new = str(text or "").strip()
    if not new:
        raise InterviewError("넣을 답을 적어 주세요.")
    old = None if _empty(answers.get(qid)) else answers.get(qid)
    out = dict(answers)
    options = [str(o) for o in question.get("options") or []]
    if kind in _TEXT_TYPES:
        limit = int(question.get("maxLength") or 300)
        value = new
        if kind == "textarea" and not replace and isinstance(old, str):
            # 덧붙이기. 들은 글이 이미 지금 답 안에 있으면 지금 답을 그대로 둔다(짧은 글 하나로
            # 긴 답 전체를 덮어쓰지 않게)
            value = old if new in old else f"{old}\n{new}"
        if len(value) > limit:
            raise InterviewError(
                f"이 질문은 {limit}자까지 넣을 수 있어요(지금 {len(value)}자). "
                "줄이거나 '지금 답 바꾸기'로 넣어 주세요."
            )
        out[qid] = value
    elif kind == "single":
        if options and not question.get("other") and new not in options:
            raise InterviewError("이 질문은 다음 가운데 하나로 넣어 주세요: " + " / ".join(options))
        if len(new) > 300:
            raise InterviewError("이 질문은 300자까지 넣을 수 있어요.")
        out[qid] = new
    elif kind in _LIST_TYPES:
        items = [line.strip() for line in new.splitlines() if line.strip()]
        long = [item for item in items if len(item) > ITEM_MAX]
        if long:
            raise InterviewError(f"한 줄은 {ITEM_MAX}자까지예요: {long[0][:20]}…")
        if kind == "multi" and options and not question.get("other"):
            wrong = [item for item in items if item not in options]
            if wrong:
                raise InterviewError(
                    f"'{wrong[0]}'은(는) 고를 수 있는 답이 아니에요: " + " / ".join(options)
                )
        before = [] if replace or not isinstance(old, list) else [v for v in old if not _empty(v)]
        merged = list(dict.fromkeys([*before, *items]))
        cap = question.get("max") or (PHRASES_MAX if kind == "phrases" else None)
        if cap and len(merged) > cap:
            raise InterviewError(
                f"이 질문은 {cap}개까지 넣을 수 있어요(합치면 {len(merged)}개). "
                "'지금 답 바꾸기'로 넣어 주세요."
            )
        out[qid] = merged
    else:
        raise InterviewError("이 질문은 여기서 넣을 수 없어요. 정보 고치기 화면에서 넣어 주세요.")
    return out


async def _check_busy(db: AsyncSession, persona: Persona) -> None:
    if persona.setup_step == SetupStep.BUILDING:
        raise InterviewError("아직 만드는 중이에요. 다 만든 뒤에 넣어 주세요.")
    for kind in ("UPDATE", "EXTEND"):
        if await jobs.is_running(db, persona.id, kind):
            raise InterviewError("다시 만드는 중이에요. 끝난 뒤에 넣어 주세요.")


async def owner_of(db: AsyncSession, persona: Persona) -> str:
    return await db.scalar(select(Person.owner_user_id).where(Person.id == persona.person_id))


async def require_interview_consent(db: AsyncSession, persona: Persona) -> None:
    """관리자가 들은 내용을 넣기 전에: 만든 분이 '인터뷰 내용을 넣는 데' 동의한 대화 상대인가.

    '나 자신' 대화 상대는 그분(=만든 분)을 인터뷰하지 않아 넣지 않는다. 동의(interview_use:<id>)는
    신청할 때 받고, 만든 분이 거두면(POST /consents) 더는 넣지 않는다. 안 되면 InterviewConflict."""
    reason = blocked_reason(persona)
    if reason:
        raise InterviewConflict(reason)
    owner = await owner_of(db, persona)
    if not await legal.has(db, owner, legal.persona_type(USE_TYPE, persona.id)):
        raise InterviewConflict(NO_USE_CONSENT)


async def consent_state(db: AsyncSession, persona: Persona) -> dict:
    """관리자 화면에 보일 만든 분의 동의(넣을 수 있는지, 종교·건강 기억 카드를 넣어도 되는지)."""
    owner = await owner_of(db, persona)

    async def has(prefix: str) -> bool:
        return await legal.has(db, owner, legal.persona_type(prefix, persona.id))

    return {
        "interviewUse": await has(USE_TYPE),
        "sensitiveReligion": await has("sensitive_religion"),
        "sensitiveHealth": await has("sensitive_health"),
    }


async def _request_for(
    db: AsyncSession, persona: Persona, request_id: str | None
) -> InterviewRequest | None:
    if not request_id:
        return None
    request = await db.get(InterviewRequest, request_id)
    if request is None or request.persona_id != persona.id:
        raise InterviewError("이 대화 상대의 신청이 아니에요.")
    return request


async def add_answer(
    db: AsyncSession,
    persona: Persona,
    question_id: str,
    text: str,
    *,
    replace: bool = False,
    request_id: str | None = None,
) -> InterviewEntry:
    """들은 답을 질문 번호로 넣는다: 기록(InterviewEntry)을 남기고 persona.answers 에 합친다.

    지침에 반영하려면 '다시 만들기'(rebuild)를 따로 누른다(답을 여러 개 넣고 한 번에 만든다).
    다 만든(READY) 대화 상대에만 넣는다: 만드는 중에는 만든 분의 답 저장이 답 전체를 바꿔
    넣어 들은 답이 말없이 사라진다. 만든 분이 인터뷰 내용을 넣는 데 동의한 대화 상대만
    (require_interview_consent).
    """
    if persona.origin == ORIGIN_IMPORT:
        raise InterviewError(
            "가져온 대화 상대는 질문 답을 넣을 수 없어요. 기억 카드로 넣어 주세요."
        )
    question = questions().get(question_id)
    if question is None:
        raise InterviewError("모르는 질문 번호예요.")
    if len(str(text or "")) > ENTRY_MAX:
        raise InterviewError(f"한 번에 {ENTRY_MAX}자까지 넣을 수 있어요.")
    await _request_for(db, persona, request_id)
    if persona.setup_step != SetupStep.READY:
        raise InterviewError(NOT_READY)
    await require_interview_consent(db, persona)
    await _check_busy(db, persona)
    category = question.get("sensitive")
    if category:
        # 종교·건강은 주인이 그 범주에 동의한 대화 상대만(질문 더 답하기와 같은 규칙)
        owner_id = await owner_of(db, persona)
        kind = legal.persona_type(f"sensitive_{category}", persona.id)
        if not await legal.has(db, owner_id, kind):
            raise InterviewError("만든 분이 이 이야기(종교·건강)에 동의하지 않아 넣을 수 없어요.")
    before = (persona.answers or {}).get(question_id)
    merged = merge_answer(persona.answers or {}, question, text, replace=replace)
    try:
        # 다른 저장 길(정보 고치기·만들기)과 같게 그 대화 상대의 기본 정보로 본다. 필수 검사는
        # 하지 않는다(질문 하나씩 넣는 길이라, 비어 있는 필수 답이 인터뷰 답 넣기를 막지 않게)
        answers = intake.validate_answers(merged, final=False, basics=persona.basics or {})
    except intake.IntakeError as error:
        raise InterviewError(str(error)) from error
    persona.answers = answers
    entry = InterviewEntry(
        request_id=request_id or None,
        persona_id=persona.id,
        question_id=question_id,
        text=str(text).strip(),
        source=ENTRY_SOURCE,
        added=_contribution(question, before, answers.get(question_id), str(text).strip()),
    )
    db.add(entry)
    await db.commit()
    return entry


# ────────────────────────── 관리자: 넣은 답 빼기 ──────────────────────────
# 만든 분이 '넣은 이야기를 빼 달라'고 하면(interview.md) 관리자가 넣은 답 기록 하나를 지운다.
# 그 기록이 persona.answers 에 더한 부분만 뺀다: 목록 질문은 새로 생긴 줄, 긴 글은 덧붙인 덩어리,
# 짧은 글·고르는 질문은 지금 답이 그 글과 같을 때만 비운다(그 뒤 만든 분이 고쳤으면 그대로 둔다).
# 지침·질문 답 기억에 반영하려면 '다시 만들기'를 누른다(넣을 때와 같다).


def _lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _contribution(question: dict, before, after, text: str) -> list[str]:
    """들은 답 하나가 지금 답에 실제로 더한 것(InterviewEntry.added)."""
    if question.get("type") in _LIST_TYPES:
        had = {v for v in before if isinstance(v, str)} if isinstance(before, list) else set()
        return [item for item in after or [] if item not in had]
    # 글·고르는 질문: 바뀌었으면 넣은 글(덧붙인 덩어리이거나 바꾼 답 전체), 그대로면 없음
    return [text] if isinstance(after, str) and after != before else []


def _cut(current: str, block: str) -> str | None:
    """긴 글에서 덧붙인 덩어리 하나를 뺀 글. 덩어리가 줄 단위로 그대로 없으면 None(고쳐 쓴 글)."""
    if current == block:
        return ""
    if current.endswith(f"\n{block}"):
        return current[: -(len(block) + 1)]
    if current.startswith(f"{block}\n"):
        return current[len(block) + 1 :]
    marker = f"\n{block}\n"
    at = current.rfind(marker)
    if at >= 0:
        return current[:at] + "\n" + current[at + len(marker) :]
    return None


async def remove_answer(db: AsyncSession, persona: Persona, entry_id: str) -> dict:
    """넣은 답 기록 하나를 지우고, 그 기록이 더한 부분만 persona.answers 에서 뺀다(commit 함).

    같은 질문에 남은 다른 인터뷰 답이 같은 줄·글을 말했으면 그 줄은 남기고 그 기록이 더한 것으로
    넘긴다(넣을 때 이미 있어 그 기록은 더한 게 없었을 수 있다). 동의를 거둔 뒤에도 뺄 수 있다.
    만들거나 다시 만드는 중에는 하지 않는다(InterviewError).

    돌려주는 것: questionId · changed(답이 바뀌었나) · answer(지금 답) · requiredMissing(꼭 받을
    답이 비었나 — 만든 분이 '정보 고치기'에서 다시 채운다).
    """
    entry = await db.get(InterviewEntry, entry_id)
    if entry is None or entry.persona_id != persona.id:
        raise InterviewNotFound("이 대화 상대에 넣은 인터뷰 답이 아니에요.")
    await _check_busy(db, persona)
    qid = entry.question_id
    question = questions().get(qid)
    answers = dict(persona.answers or {})
    current = answers.get(qid)
    # 같은 질문에 남은 다른 인터뷰 답(넣은 차례대로)
    others = list(
        (
            await db.scalars(
                select(InterviewEntry)
                .where(
                    InterviewEntry.persona_id == persona.id,
                    InterviewEntry.question_id == qid,
                    InterviewEntry.id != entry.id,
                )
                .order_by(InterviewEntry.created_at)
            )
        ).all()
    )
    kind = (question or {}).get("type")
    if entry.added is not None:
        mine = list(entry.added)
    else:
        mine = _lines(entry.text) if kind in _LIST_TYPES else [entry.text.strip()]

    def hand_over(part: str, claims) -> bool:
        """같은 것을 말한 다른 기록이 남아 있으면 그 부분은 남기고, 그 기록이 더한 것으로 넘긴다."""
        for other in others:
            if claims(other):
                if other.added is not None and part not in other.added:
                    other.added = [*other.added, part]
                return True
        return False

    changed = False
    if kind in _LIST_TYPES and isinstance(current, list):
        drop = [
            item
            for item in mine
            if item in current and not hand_over(item, lambda o, i=item: i in _lines(o.text))
        ]
        if drop:
            current = [v for v in current if v not in drop]
            changed = True
    elif kind is not None and isinstance(current, str):
        for block in mine:
            if kind == "textarea":
                rest = _cut(current, block)
            else:
                # 짧은 글·고르는 질문은 지금 답이 그 글과 같을 때만 비운다
                rest = "" if current == block else None
            if rest is None or hand_over(block, lambda o, b=block: o.text.strip() == b):
                continue
            current, changed = rest, True
    if changed:
        if current:
            answers[qid] = current
        else:
            answers.pop(qid, None)
        # 다른 저장 길과 같게 다듬는다(빈 조심할 이야기 등급은 미리 채운 값으로 돌아간다)
        answers = intake.validate_answers(answers, final=False, basics=persona.basics or {})
        persona.answers = answers
    await db.delete(entry)
    await db.commit()
    return {
        "questionId": qid,
        "changed": changed,
        "answer": answers.get(qid),
        "requiredMissing": qid in intake.required_ids(persona.basics or {})
        and not answers.get(qid),
    }


async def add_memory(
    db: AsyncSession, persona: Persona, *, text: str, title: str | None, asked_only: bool = False
) -> Memory:
    """기억 카드 하나(source=INTERVIEW). 바로 임베딩해서 다음 대화부터 찾힌다.

    임베딩을 먼저 하고 저장한다(실패하면 아무것도 남기지 않는다 — 벡터 없는 기억은 다시 만들기도
    채우지 않아 대화에서 찾히지 않는다). 만든 분이 인터뷰 내용을 넣는 데 동의한 대화 상대만
    (require_interview_consent). 관리자가 가져온 대화 상대는 기억을 모두 관리자가 넣는 길이라
    따로 보지 않는다.
    """
    body = str(text or "").strip()
    if not body:
        raise InterviewError("기억 내용을 적어 주세요.")
    if len(body) > MEMORY_MAX:
        raise InterviewError(f"기억 카드 하나는 {MEMORY_MAX}자까지예요. 나눠서 넣어 주세요.")
    if persona.origin != ORIGIN_IMPORT:
        await require_interview_consent(db, persona)
    # 바로 해외 회사(OpenAI)로 임베딩하므로 처리 게이트를 본다: 멈췄거나, 아직 다 만들지 않은 살아
    # 계신 분의 대화 상대인데 그분 동의 녹음이 확인되기 전이면 넣지 않는다(관리자가 부른다)
    found = await gate.block(db, persona, "text")
    if found is not None:
        raise InterviewConflict(found.message)
    topic = _text(title, 60) or MEMORY_TAG
    person_owner = await owner_of(db, persona)
    memory_id = str(uuid4())
    try:
        vectors = await ai_service.embed(
            job_id=str(uuid4()),
            items=[{"memorySegmentId": memory_id, "embeddingIndex": 0, "text": body}],
        )
    except AIServerNotConfigured as error:
        raise InterviewError("AI 서버가 연결되지 않아 기억을 넣지 못했어요.") from error
    vector = vectors.get(memory_id)
    if not vector:
        raise InterviewError("기억을 찾기 쉽게 정리하지 못했어요. 잠시 뒤 다시 넣어 주세요.")
    memory = Memory(
        id=memory_id,
        person_id=persona.person_id,
        created_by_user_id=person_owner,
        title=intake.memory_title(topic, intake.ASKED_ONLY if asked_only else None)[:120],
        memory_type="EPISODE",
        related_people=[],
        body_markdown=body,
        tags=[MEMORY_TAG, topic][:10] if topic != MEMORY_TAG else [MEMORY_TAG],
        source=MEMORY_SOURCE,
        embedding=vector,
        embedding_status="READY",
    )
    db.add(memory)
    await db.commit()
    return memory


async def remove_memory(db: AsyncSession, persona: Persona, memory_id: str) -> None:
    """잘못 넣은 기억 카드를 지운다(인터뷰로 넣은 것만)."""
    memory = await db.get(Memory, memory_id)
    if memory is None or memory.person_id != persona.person_id or memory.source != MEMORY_SOURCE:
        raise InterviewNotFound("이 대화 상대의 인터뷰 기억이 아니에요.")
    await db.delete(memory)
    await db.commit()


async def memories(db: AsyncSession, persona: Persona) -> list[Memory]:
    return list(
        (
            await db.scalars(
                select(Memory)
                .where(Memory.person_id == persona.person_id, Memory.source == MEMORY_SOURCE)
                .order_by(Memory.created_at)
            )
        ).all()
    )


async def entries(db: AsyncSession, persona_id: str) -> list[InterviewEntry]:
    return list(
        (
            await db.scalars(
                select(InterviewEntry)
                .where(InterviewEntry.persona_id == persona_id)
                .order_by(InterviewEntry.created_at)
            )
        ).all()
    )


async def rebuild(db: AsyncSession, persona: Persona):
    """'다시 만들기': 지금 답으로 지침·질문 답 기억을 다시 만든다(기존 UPDATE 작업)."""
    from app.services import persona_builder

    if persona.origin == ORIGIN_IMPORT:
        raise InterviewError("가져온 대화 상대는 다시 만들지 않아요(기억 카드는 바로 쓰여요).")
    if persona.setup_step != SetupStep.READY:
        raise InterviewError("다 만든 대화 상대만 다시 만들 수 있어요.")
    await _check_busy(db, persona)
    return await jobs.start(db, persona.id, "UPDATE", persona_builder.run_update)


async def job_state(db: AsyncSession, persona_id: str) -> dict | None:
    job = await jobs.latest(db, persona_id, "UPDATE")
    if job is None:
        return None
    return {
        "status": job.status,
        "detail": job.detail,
        "percent": job.percent,
        "error": job.error,
        "createdAt": job.created_at,
        "running": job.status == JobStatus.RUNNING,
    }
