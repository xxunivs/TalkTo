"""대답 평가(말풍선 ⋯)와 대화 전체 한 줄. 반영 규칙은 app/services/corrections.py.

예전 화면(rating·tags 만 보냄)도 그대로 된다: 고침이 없으면 state=none 으로 저장만 한다.
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core import ratelimit
from app.core.config import get_settings
from app.db.models import Conversation, ConversationFeedback, Feedback, Message
from app.schemas.api import (
    ConversationFeedbackCreate,
    ConversationFeedbackView,
    FeedbackCreate,
    FeedbackView,
)
from app.schemas.common import Envelope
from app.services import corrections, gaps, gate, sharing

router = APIRouter(tags=["Feedback"])

APPLIED_BY_OWNER = "만든 분이 이미 넣은 고침이에요. 바꾸려면 만든 분께 말씀해 주세요."


@router.post("/messages/{message_id}/feedback", response_model=Envelope[FeedbackView])
async def save_feedback(
    message_id: str,
    payload: FeedbackCreate,
    user: CurrentUser,
    db: DB,
    background: BackgroundTasks,
):
    """내 대화방의 대답 하나를 평가한다(다시 보내면 고친다).

    STYLE·CONTENT 에 고침 한 줄이 있으면 만든 분은 바로 반영하고(state=applied), 함께 쓰는
    가족은 만든 분 확인을 기다린다(state=pending_owner). 만든 분의 '내용이 틀려요'는 대답 뒤
    작업으로 '더 알려 주실 기억' 후보도 된다. 함께 쓰는 가족은 고침 없이 '내용이 틀려요'만
    골랐을 때 후보가 되고(가족이 한 말은 질문에 옮기지 않게 from_member — 질문은 만든 분 목록에만
    생기고 아무것도 반영하지 않는다), 고침이 있으면 만든 분이 확인할 때 후보가 된다.
    만든 분이 이미 넣은 가족의 고침은 그 가족이 바꾸거나 거둘 수 없다(409).
    """
    ratelimit.FEEDBACK.check(user.id)
    message = await db.scalar(
        select(Message)
        .join(Conversation)
        .where(Message.id == message_id, Conversation.user_id == user.id)
    )
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "메시지를 찾지 못했어요.")
    conversation = await db.get(Conversation, message.conversation_id)
    # 내 방이어도 이 대화 상대에 들어갈 수 있는지 다시 본다(내보내진 가족은 404)
    access = await sharing.chat_access(db, conversation.persona_id, user)
    persona = access.persona
    user_id = user.id
    tags = list(dict.fromkeys(payload.tags))
    correction = corrections.clean_correction(payload.correction)
    item = await db.scalar(
        select(Feedback).where(Feedback.message_id == message_id, Feedback.user_id == user_id)
    )
    before = None if item is None else (list(item.tags or []), item.correction)
    member = access.member
    if member and item is not None and item.state == corrections.APPLIED:
        if before != (tags, correction):
            # 만든 분이 확인해 넣은 말투 예시·기억을 가족 혼자 거두지 못하게
            raise HTTPException(status.HTTP_409_CONFLICT, APPLIED_BY_OWNER)
    if item is None:
        item = Feedback(message_id=message_id, user_id=user_id)
        db.add(item)
    item.rating = payload.rating
    item.tags = tags
    item.comment = payload.comment
    item.correction = correction
    item.persona_id = persona.id
    item.reviewed = False
    content_signal = False
    if before != (tags, correction):
        # 고침 반영(기억 임베딩)·빈틈 질문은 해외 회사(OpenAI)를 부른다. 멈춘 대화 상대는
        # 하지 않는다
        await gate.require(db, persona, "text", member=member)
        await db.flush()
        # 고쳐 다시 보냈으면 앞서 반영한 것을 거두고 새로 정한다
        await corrections.clear_applied(db, item)
        item.applied_at = item.decided_at = None
        reply = message.role == "assistant"
        todo = corrections.wants(tags, correction) if reply else set()
        if not todo:
            item.state = corrections.NONE
        elif member:
            item.state = corrections.PENDING  # 만든 분이 확인하기 전에는 아무것도 반영하지 않는다
        else:
            await corrections.apply(db, persona, item, message)
        if reply and corrections.CONTENT in tags:
            bare = corrections.CONTENT not in todo
            if bare:
                # 고침 없이 '내용이 틀려요'만 골라도 그 대답에 쓰인 기억에 끌 후보 표시를 한다
                # (관리자만 보는 표시라 함께 쓰는 가족이 골라도 한다. 지우거나 숨기지 않는다)
                await corrections.flag_wrong(db, persona, message)
            # 처음 '내용이 틀려요'를 고른 때만 빈틈 후보로 보낸다. 가족의 고침은 만든 분이
            # 확인할 때 보낸다(app/api/v1/gaps.py approve_correction)
            if bare or not member:
                content_signal = before is None or corrections.CONTENT not in before[0]
    await db.commit()
    await db.refresh(item)
    if content_signal and get_settings().gap_questions_enabled:
        asked = await corrections.asked_before(db, message)
        if asked:
            background.add_task(
                gaps.after_turn,
                persona_id=persona.id,
                message_id=message.id,
                user_message=asked,
                reply=message.content,
                signal="feedback",
                correction=correction,
                # 가족이 한 말은 만든 분이 보는 질문에 옮기지 않게 한다
                from_member=member,
            )
    return Envelope(data=FeedbackView.model_validate(item))


@router.post(
    "/conversations/{conversation_id}/feedback",
    response_model=Envelope[ConversationFeedbackView],
    status_code=201,
)
async def conversation_feedback(
    conversation_id: str, payload: ConversationFeedbackCreate, user: CurrentUser, db: DB
):
    """대화 전체에 한 줄 남기기(대화 화면 위 작은 단추). 관리자만 본다. 남길 때마다 쌓인다."""
    ratelimit.FEEDBACK.check(user.id)
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    if conversation is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "대화를 찾지 못했어요. 화면을 새로고침해 주세요."
        )
    await sharing.chat_access(db, conversation.persona_id, user)
    comment = " ".join(payload.comment.split())
    if not comment:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "남길 말을 적어 주세요.")
    item = ConversationFeedback(
        conversation_id=conversation.id,
        user_id=user.id,
        persona_id=conversation.persona_id,
        comment=comment,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return Envelope(data=ConversationFeedbackView.model_validate(item))
