"""'더 알려 주실 기억'(app/services/gaps.py)과 가족 고침 확인(app/services/corrections.py).

만든 분(주인)과 함께 쓰는 가족(sharing.chat_access)이 쓴다. 그 밖의 사람은 404. 만든 분만 하는 일
('가족에게 물어보기'·가족 답/고침 확인)은 가족이 부르면 404 다(무엇이 있는지 알리지 않는다).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.api.deps import DB, CurrentUser
from app.core import ratelimit
from app.core.config import get_settings
from app.schemas.api import CorrectionDecision, GapAnswer, GapAskFamily, GapDecision
from app.schemas.common import Envelope
from app.services import corrections, gaps, gate, sharing

router = APIRouter(tags=["Gap questions"])

BASE = "/personas/{persona_id}/gap-questions"


def _owner_only(access: sharing.ChatAccess) -> None:
    if access.member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, gaps.NOT_FOUND)


async def _lists(db: DB, access: sharing.ChatAccess, user_id: str) -> dict:
    persona_id = access.persona.id
    if access.member:
        lists = await gaps.member_lists(db, persona_id, user_id)
        return {
            "role": "member",
            **lists,
            "pendingCorrections": [],
            "openCount": len(lists["questions"]),
            "canAskFamily": False,
        }
    lists = await gaps.owner_lists(db, persona_id, user_id)
    return {
        "role": "owner",
        **lists,
        "pendingCorrections": await corrections.pending(db, persona_id),
        "openCount": len(lists["questions"]),
        # '가족에게 물어보기'는 함께 쓰는 가족이 있을 때만
        "canAskFamily": await sharing.joined_count(db, persona_id) > 0,
    }


@router.get(BASE, response_model=Envelope[dict])
async def list_gap_questions(persona_id: str, user: CurrentUser, db: DB):
    """만든 분: 열린 질문 + 확인을 기다리는 가족 답·가족 고침.
    가족: 물어봐 달라고 한 질문 + 내 답."""
    access = await sharing.chat_access(db, persona_id, user)
    return Envelope(data=await _lists(db, access, user.id))


@router.post(BASE + "/{question_id}/answer", response_model=Envelope[dict])
async def answer_gap_question(
    persona_id: str, question_id: str, payload: GapAnswer, user: CurrentUser, db: DB
):
    """답하기(글 또는 🎤로 옮긴 글). 만든 분은 바로 기억(source=GAP)이 되고, 가족은 만든 분
    확인을 기다린다."""
    ratelimit.FEEDBACK.check(user.id)
    access = await sharing.chat_access(db, persona_id, user)
    # 만든 분의 답은 바로 기억이 되어 해외 회사(OpenAI)로 임베딩한다. 멈춘 대화 상대는 받지 않는다
    # (가족은 403 share_paused — 처리 게이트)
    await gate.require(db, access.persona, "text", member=access.member)
    user_id = user.id
    row = await gaps.get_question(db, persona_id, question_id)
    member = access.member
    row = await gaps.answer(db, access.persona, row, user_id, payload.text, member=member)
    labels = await gaps.member_labels(db, persona_id, user_id) if member else None
    return Envelope(data=gaps.view(row, user_id, labels=labels))


@router.post(BASE + "/{question_id}/dismiss", response_model=Envelope[dict])
async def dismiss_gap_question(persona_id: str, question_id: str, user: CurrentUser, db: DB):
    """'잘 모르겠어요'. 만든 분은 질문을 닫고, 가족은 그 가족에게만 숨긴다."""
    access = await sharing.chat_access(db, persona_id, user)
    user_id = user.id
    row = await gaps.get_question(db, persona_id, question_id)
    member = access.member
    row = await gaps.dismiss(db, row, user_id, member=member)
    labels = await gaps.member_labels(db, persona_id, user_id) if member else None
    return Envelope(data=gaps.view(row, user_id, labels=labels))


@router.post(BASE + "/{question_id}/ask-family", response_model=Envelope[dict])
async def ask_family(
    persona_id: str,
    question_id: str,
    user: CurrentUser,
    db: DB,
    payload: GapAskFamily | None = None,
):
    """'가족에게 물어보기'(만든 분, 함께 쓰는 가족이 있을 때만). {"ask": false} 면 거둔다."""
    access = await sharing.chat_access(db, persona_id, user)
    _owner_only(access)
    user_id = user.id
    row = await gaps.get_question(db, persona_id, question_id)
    ask = True if payload is None else payload.ask
    members = await sharing.joined_count(db, persona_id)
    row = await gaps.ask_family(db, access.persona, row, ask, members)
    return Envelope(data=gaps.view(row, user_id))


def _seen_answer(payload: GapDecision | None) -> tuple[str | None, datetime | None] | None:
    return None if payload is None else (payload.answer_text, payload.answered_at)


@router.post(BASE + "/{question_id}/approve", response_model=Envelope[dict])
async def approve_gap_answer(
    persona_id: str,
    question_id: str,
    user: CurrentUser,
    db: DB,
    payload: GapDecision | None = None,
):
    """가족 답을 기억에 넣는다(만든 분).

    본문 {answerText, answeredAt} 는 만든 분이 목록에서 본 답이다. 그 사이에 바뀌었으면 409
    (gaps.approve) — 만든 분이 보지 않은 답은 기억이 되지 않는다."""
    ratelimit.FEEDBACK.check(user.id)
    access = await sharing.chat_access(db, persona_id, user)
    _owner_only(access)
    await gate.require(db, access.persona, "text")
    user_id = user.id
    row = await gaps.get_question(db, persona_id, question_id)
    row = await gaps.approve(db, access.persona, row, _seen_answer(payload))
    return Envelope(data=gaps.view(row, user_id, await gaps.member_names(db, persona_id)))


@router.post(BASE + "/{question_id}/reject", response_model=Envelope[dict])
async def reject_gap_answer(
    persona_id: str,
    question_id: str,
    user: CurrentUser,
    db: DB,
    payload: GapDecision | None = None,
):
    """가족 답을 넣지 않는다(만든 분). 질문은 다시 열린다.
    본문은 approve 와 같다(바뀌었으면 409 — 본 적 없는 답을 지우지 않게)."""
    access = await sharing.chat_access(db, persona_id, user)
    _owner_only(access)
    user_id = user.id
    row = await gaps.get_question(db, persona_id, question_id)
    row = await gaps.reject(db, row, _seen_answer(payload))
    return Envelope(data=gaps.view(row, user_id, {}))


# ────────────────────────── 가족 고침 확인 ──────────────────────────

CORRECTIONS = "/personas/{persona_id}/corrections/{feedback_id}"


def _correction_view(item) -> dict:
    return {
        "id": item.id,
        "messageId": item.message_id,
        "tags": item.tags or [],
        "correction": item.correction,
        "state": item.state,
        "appliedAt": item.applied_at,
        "decidedAt": item.decided_at,
    }


def _seen(payload: CorrectionDecision | None) -> tuple[list[str], str | None] | None:
    return None if payload is None else (payload.tags, payload.correction)


@router.post(CORRECTIONS + "/approve", response_model=Envelope[dict])
async def approve_correction(
    persona_id: str,
    feedback_id: str,
    user: CurrentUser,
    db: DB,
    background: BackgroundTasks,
    payload: CorrectionDecision | None = None,
):
    """함께 쓰는 가족의 말투·내용 고침을 반영한다(만든 분). 내용 고침이면 빈틈 후보도 된다.

    본문 {correction, tags} 는 만든 분이 목록에서 본 고침이다. 가족이 그 사이에 바꿨으면 409
    (corrections.pending_item) — 만든 분이 보지 않은 글은 반영하지 않는다."""
    ratelimit.FEEDBACK.check(user.id)
    access = await sharing.chat_access(db, persona_id, user)
    _owner_only(access)
    # 반영은 기억 임베딩·빈틈 질문으로 해외 회사(OpenAI)를 부른다. 멈춘 대화 상대는 하지 않는다
    await gate.require(db, access.persona, "text")
    item, message = await corrections.pending_item(db, persona_id, feedback_id, _seen(payload))
    content = await corrections.apply(db, access.persona, item, message)
    item.decided_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(item)
    if content and get_settings().gap_questions_enabled:
        background.add_task(
            gaps.after_turn,
            persona_id=persona_id,
            message_id=message.id,
            user_message=await corrections.asked_before(db, message),
            reply=message.content,
            signal="feedback",
            correction=item.correction,
            # 확인을 기다리던 고침은 함께 쓰는 가족의 것이다(가족의 말·고친 말을 질문에 옮기지 않게)
            from_member=True,
        )
    return Envelope(data=_correction_view(item))


@router.post(CORRECTIONS + "/reject", response_model=Envelope[dict])
async def reject_correction(
    persona_id: str,
    feedback_id: str,
    user: CurrentUser,
    db: DB,
    payload: CorrectionDecision | None = None,
):
    """함께 쓰는 가족의 고침을 반영하지 않는다(만든 분). 본문은 approve 와 같다(바뀌었으면 409)."""
    access = await sharing.chat_access(db, persona_id, user)
    _owner_only(access)
    item, _message = await corrections.pending_item(db, persona_id, feedback_id, _seen(payload))
    item.state = corrections.REJECTED
    item.decided_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(item)
    return Envelope(data=_correction_view(item))
