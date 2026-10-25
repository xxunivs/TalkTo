import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import DB, CurrentUser
from app.core import ratelimit
from app.core.config import get_settings
from app.db.models import (
    ORIGIN_IMPORT,
    Conversation,
    LifeStatus,
    Message,
    Person,
    Persona,
    SetupStep,
)
from app.schemas.api import (
    ConversationCreate,
    ConversationDetail,
    ConversationView,
    MessageExchange,
    MessageView,
    TextMessageCreate,
)
from app.schemas.common import Envelope
from app.services import (
    ages,
    analytics,
    audio,
    billing,
    corrections,
    gaps,
    gate,
    intake,
    persona_builder,
    sharing,
)
from app.services.ai import AI_TIMED_OUT, TIMEOUT_CODE, AIServerError, ai_service
from app.services.memory_search import count_memories, search_memories_scored
from app.services.storage import get_storage, resolve_audio_url

router = APIRouter(prefix="/conversations", tags=["Conversations"])
speech_router = APIRouter(tags=["Conversations"])
logger = logging.getLogger(__name__)

NOT_READY = "아직 만드는 중이에요. 다 만든 뒤에 대화할 수 있어요."
# 대화 횟수를 다 쓴 뒤 말로 보냈을 때(글로만 대답한다).
# 함께 쓰는 가족에게는 함께 쓰는 횟수라고 한다.
OWNER_TEXT_ONLY = (
    "대화 횟수를 다 써서 지금은 목소리 대신 글로만 대답해요. 말 대신 글로 보내 주세요."
)
MEMBER_TEXT_ONLY = (
    "가족이 함께 쓰는 대화 횟수를 다 써서 지금은 글로만 대답해요. 말 대신 글로 보내 주세요."
)
# 목소리 메시지 한 개 크기 상한(앞에서 body_limit 이 11MB 로 먼저 막는다). 길이는 따로 본다
# (max_voice_message_seconds, 5분). 화면이 고르는 형식 가운데 가장 큰 wav(16kHz mono)로도 5분이
# 9.6MB 라 안에 든다(webm·m4a 는 훨씬 작다).
VOICE_MAX_BYTES = 10 * 1024 * 1024
# 말로 보낸 것을 글로 옮긴 결과의 글자 수 상한(글로 보내기와 같다)
STT_MAX_CHARS = 4000
# 서버 전체에서 동시에 글로 옮기는 목소리 메시지 수(AI 서버 스레드·메모리를 한 사람이 다 쓰지 않게)
STT_SLOTS = 3
# 보내고 대답을 받기까지 쓸 수 있는 시간(초). CloudFront 가 120초에 끊기 전에 끝내야, 끊긴 뒤에
# 저장·횟수만 남는 일이 없다.
EXCHANGE_BUDGET_SECONDS = 110
# 같은 대답을 목소리로 만드는 중일 때 다른 요청이 기다리는 시간(초)
SPEECH_WAIT_SECONDS = 60

VOICE_BUSY_USER = "앞서 보낸 녹음을 아직 듣고 있어요. 대답이 온 뒤에 보내 주세요."
VOICE_BUSY = "지금 말씀을 글로 옮기는 일이 많아요. 잠시 뒤 다시 보내 주세요."
SPEECH_FAILED = "지금은 목소리로 들려 드리지 못했어요. 잠시 뒤 다시 들어 주세요."
# 체험을 쓰지 않은 대화 상대의 첫 결제 7일 안 전액 환불(이용약관 5번 · 2026-10-22 대표 결정 1)
NO_TRIAL_REFUND = (
    "무료 체험을 쓰지 않은 대화 상대는 처음 결제한 날부터 7일 안에 말씀하시면 대화하셨더라도 "
    "전액 돌려드려요."
)

# 대화에 넣을 지난 메시지 수(최근 6턴). 앞말을 이어받게 한다.
HISTORY_MESSAGES = 12
# AI 미성년 안전 검사가 막거나 바꾼 대답(AI pipeline/safety/minor_guard.py FALLBACK 과 같은 글 —
# 존댓말·반말). 이 대답이 온 턴은 빈틈 질문을 만들지 않는다
MINOR_FALLBACKS = (
    "그 이야기는 잘 모르겠어요. 우리 다른 이야기 해요.",
    "그 얘기는 잘 모르겠어. 우리 다른 얘기 하자.",
)

# 지금 목소리 메시지를 보내는 사람(한 사람에 하나씩)과 글로 옮기는 수. 서버 한 대·이벤트 루프
# 하나라 잠금 없이 센다.
_voice_senders: set[str] = set()
_stt_active = 0
# 지금 목소리로 만드는 중인 대답(message id → 끝나면 켜지는 신호)
_speaking: dict[str, asyncio.Event] = {}


def message_view(item: Message) -> MessageView:
    view = MessageView.model_validate(item)
    view.audio_url = resolve_audio_url(item.audio_url)
    return view


def conversation_view(item: Conversation) -> ConversationView:
    return ConversationView(
        id=item.id,
        persona_id=item.persona_id,
        channel=item.channel,
        title=item.title,
        started_at=item.created_at,
        last_message_at=item.last_message_at,
    )


async def owned_conversation(db: DB, conversation_id: str, user_id: str, messages: bool = False):
    query = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.user_id == user_id
    )
    if messages:
        query = query.options(selectinload(Conversation.messages))
    item = await db.scalar(query)
    if item is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "대화를 찾지 못했어요. 화면을 새로고침해 주세요."
        )
    return item


@router.post("", response_model=Envelope[ConversationView], status_code=201)
async def create_conversation(payload: ConversationCreate, user: CurrentUser, db: DB):
    access = await sharing.chat_access(db, payload.persona_id, user)
    if access.persona.setup_step != SetupStep.READY:
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_READY)
    values = payload.model_dump()
    if access.member:
        # 가족의 방 이름은 그 가족이 부르는 말(성함이 대화 목록으로 새지 않게)
        values["title"] = (access.share.profile or {}).get("addressTerm")
    item = Conversation(user_id=user.id, **values)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return Envelope(data=conversation_view(item))


@router.get("", response_model=Envelope[list[ConversationView]])
async def list_conversations(user: CurrentUser, db: DB):
    rows = (
        await db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.created_at.desc())
        )
    ).all()
    return Envelope(data=[conversation_view(x) for x in rows])


@router.get("/{conversation_id}", response_model=Envelope[ConversationDetail])
async def conversation_detail(conversation_id: str, user: CurrentUser, db: DB):
    item = await owned_conversation(db, conversation_id, user.id, messages=True)
    base = conversation_view(item).model_dump()
    return Envelope(
        data=ConversationDetail(**base, messages=[message_view(x) for x in item.messages])
    )


# 감정 안전선용 고인 여부. 모르면 None 으로 보내고 AI 서버가 고인으로 본다.
DECEASED = {LifeStatus.LIVING: False, LifeStatus.DECEASED: True}


async def exchange(
    db: DB,
    conversation: Conversation,
    user_content: str,
    audio_url: str | None = None,
    *,
    text_only: bool = False,
    access: sharing.ChatAccess | None = None,
    deadline: float | None = None,
    background: BackgroundTasks | None = None,
):
    """한 번 말하고 한 번 대답 듣기. 대화 횟수는 부르는 쪽이 미리 잡아 둔다(start_conversation).

    가족이 고쳐 준 말투(최근 10개)를 함께 보내고, 기억 빈틈 표시를 부탁한다
    (gap_questions_enabled). 빈틈 신호(AI 표시·기억 검색이 빈 묻는 말)가 있으면 대답을 저장한
    뒤 background 에 '더 알려 주실 기억' 질문 만들기를 건다(gaps.after_turn — 응답을 보낸 뒤
    돈다. 대답 응답에는 싣지 않는다).

    deadline(monotonic 초)이 있으면 대답을 그때까지만 기다린다(넘으면 AIServerError, 부르는 쪽이
    되돌리고 횟수를 돌려준다). 읽기를 마친 뒤 AI 를 기다리는 동안에는 DB 트랜잭션을 열어 두지
    않는다(연결을 붙잡지 않게). 지난 대화 가운데 빈 글은 보내지 않는다(AI 서버가 거절한다).

    text_only 면 대화 횟수를 다 쓴 뒤의 대답이라 글로만 준다(목소리로 만들지 않는다).
    함께 쓰는 가족이 말하면(access.member) 그 가족을 '지금 대화하는 사람'으로 놓은 지침과 그 가족의
    이름(speakerHint, 가져온 것은 빼고)을 보내고, 주인이 적은 기억에는 누가 적었는지 붙인다.
    주인이 말하면 보내는 것이 예전과 같다.
    """
    persona = await db.get(Persona, conversation.persona_id)
    member = access is not None and access.member
    instructions = persona.instructions
    speaker_hint = None
    if member:
        # 사용자 말을 넣기 전에 정한다(조립이 실패하면 부르는 쪽이 되돌리고 횟수를 돌려준다)
        instructions = await sharing.member_instructions(db, access)
        if persona.origin != ORIGIN_IMPORT:
            speaker_hint = (access.share.profile or {}).get("myName")
    # 가장 최근 메시지들을 시간순으로 (예전에는 가장 오래된 40개를 넣고 있었다)
    history_rows = list(
        reversed(
            (
                await db.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation.id)
                    .order_by(Message.created_at.desc())
                    .limit(HISTORY_MESSAGES)
                )
            ).all()
        )
    )
    history = [
        {"role": row.role, "content": row.content}
        for row in history_rows
        if (row.content or "").strip()
    ]
    person = await db.get(Person, persona.person_id)
    # 앞 대화도 넘긴다: AI 서버가 "얼마나 넣어?" 같은 말을 앞 대화로 채워 찾는다
    # (AI QUERY_REWRITE)
    memories, top_score = await search_memories_scored(
        db, person.id, user_content, history=history
    )
    # 검색은 상위 몇 개만 보낸다. 총수로 AI 서버가 기억이 적은 페르소나를 가린다(memoryTotal)
    memory_total = await count_memories(db, person.id)
    titles = {row.id: row.title for row in memories}
    # 함께 쓰는 가족과의 대화로는 '더 알려 주실 기억' 질문을 만들지 않는다(빈틈 표시도 부탁하지
    # 않는다). 그 가족의 대화 내용이 만든 분이 보는 질문으로 옮겨지지 않게(약관 family_share:
    # 만든 분도 가족의 대화를 볼 수 없어요). 가족이 직접 '내용이 틀려요'를 고른 대답은
    # feedback 에서 후보가 된다.
    flag_gaps = get_settings().gap_questions_enabled and not member
    # 고인(모르면 고인으로 본다)이면 '기억이 안 난다'가 든 고친 말투는 보내지 않는다(잠금)
    styles = await corrections.style_examples(
        db, persona.id, deceased=DECEASED.get(person.life_status) is not False
    )
    if member and not intake.is_self(persona.basics or {}):
        owner = sharing.owner_label(access)
        titles = {
            row.id: sharing.member_memory_title(row.title, row.source, owner) for row in memories
        }
    # 읽기를 마쳤다(가족 지침을 새로 조립했으면 그것도 저장한다). 대답을 기다리는 동안 연결을
    # 트랜잭션 속에 붙잡아 두지 않는다.
    await db.commit()
    minor = ages.is_minor_now(persona.basics or {}, ages.today_kst())
    timeout = None
    if deadline is not None:
        timeout = deadline - monotonic()
        if timeout < 5:
            raise AIServerError(504, AI_TIMED_OUT, TIMEOUT_CODE)
    # 시각을 직접 넣는다. 기본값은 저장할 때 정해져서 사용자 말과 대답이 같은 시각이 되면
    # 대화 순서가 뒤바뀌었다.
    asked_at = datetime.now(UTC)
    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=user_content,
        audio_url=audio_url,
        created_at=asked_at,
    )
    db.add(user_message)
    reply = await ai_service.reply(
        session_id=conversation.id,
        message=user_content,
        history=history,
        memories=[
            {"id": row.id, "title": titles[row.id], "content": row.body_markdown, "tags": row.tags}
            for row in memories
        ],
        persona={
            "subject_id": person.id,
            "name": persona.name,
            "description": persona.description,
            "speaking_style": persona.speaking_style,
            "voice_id": persona.voice_id,
            "instructions": instructions,
            "deceased": DECEASED.get(person.life_status),
            "fixed_prompt": persona.origin == ORIGIN_IMPORT,
            # 만 19세 미만 아이의 대화 상대면 AI 미성년 안전 규칙(생년월은 보내지 않는다)
            "minor": minor,
        },
        **({"speaker_hint": speaker_hint} if speaker_hint else {}),
        **({"timeout": timeout} if timeout is not None else {}),
        flag_gaps=flag_gaps,
        style_examples=styles,
        memory_total=memory_total,
    )
    content = reply.content
    assistant = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=content,
        retrieved_memory_ids=reply.memory_ids,
        latency_ms=reply.latency_ms,
        emotion=reply.emotion,
        # 목소리로 읽을 글(연기 표시 포함). 화면·다음 대화 기록에는 content 만 쓴다
        speech_text=reply.speech_text,
        text_only=text_only or None,
        created_at=max(datetime.now(UTC), asked_at + timedelta(milliseconds=1)),
    )
    db.add(assistant)
    conversation.last_message_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(user_message)
    await db.refresh(assistant)
    signal = gaps.signal_for(reply.memory_gap, top_score, user_content) if flag_gaps else None
    if minor and content in MINOR_FALLBACKS:
        # 아이의 대화 상대에서 AI 안전 검사가 막은 턴: 그 말을 '더 알려 주실 기억' 질문으로 옮겨
        # 해외 회사로 다시 보내지 않는다
        signal = None
    if signal is not None and background is not None:
        background.add_task(
            gaps.after_turn,
            persona_id=persona.id,
            message_id=assistant.id,
            user_message=user_content,
            reply=content,
            signal=signal[0],
            topic=signal[1],
            # 함께 쓰는 가족의 말이면 그 가족이 한 말을 만든 분이 보는 질문에 옮기지 않게 한다
            from_member=member,
        )
    return MessageExchange(
        user_message=message_view(user_message),
        assistant_message=message_view(assistant),
    )


async def require_active(db, access: sharing.ChatAccess) -> billing.Entitlement:
    """이 페르소나의 체험이 끝났고 구독도 없으면 대화를 멈춘다. 만든 페르소나·대화 기록은 남긴다.

    권리는 늘 주인 것으로 본다. 함께 쓰는 가족은 주인의 체험을 열지 않고, 멈췄으면 403
    share_paused(까닭과 한 문장 — 요금제 고르기 안내 없이)를 받는다. 나눈 대화는 그대로 본다.
    """
    persona = access.persona
    if persona.setup_step != SetupStep.READY:
        # 체험은 다 만든 뒤부터다. 만드는 중인 페르소나로 대화하면 체험 상한을 넘어 쓸 수 있다.
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_READY)
    if access.member:
        ent, reason = await sharing.member_entitlement(db, access)
        if reason:
            raise sharing.paused_error(access, reason, ent)
        return ent
    ent = await billing.entitlement(db, access.owner, persona)
    if not ent.active:
        if ent.status == "NO_TRIAL":
            text = (
                "무료 체험은 계정 하나에 대화 상대 "
                f"{get_settings().trial_personas_per_account}명까지예요. "
                "이 대화 상대는 요금제를 고르면 대화할 수 있어요. " + NO_TRIAL_REFUND
            )
        else:
            sub = await billing.get_subscription(db, persona.id)
            had_plan = bool(sub and sub.current_period_start)
            text = (
                "구독이 끝났어요." if had_plan else "무료 체험이 끝났어요."
            ) + " 요금제를 고르면 이어서 대화할 수 있어요."
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, text)
    return ent


def _date(value: datetime) -> str:
    k = value.astimezone(billing.KST)
    return f"{k.month}월 {k.day}일"


def _exhausted(ent: billing.Entitlement, *, member: bool = False) -> HTTPException:
    """대화 횟수를 다 쓰고 글 대답의 공정 사용 상한까지 넘었을 때.

    함께 쓰는 가족에게는 요금제 이야기 없이 함께 쓰는 횟수라고만 알린다(결제는 주인이 한다).
    """
    if member:
        when = f" {_date(ent.resets_at)}에 다시 채워져요." if ent.resets_at is not None else ""
        if ent.status == "PRELAUNCH":
            text = "가족이 함께 쓰는 무료 대화를 이번에 모두 썼어요." + when
        else:
            text = "이번에 가족이 함께 쓸 수 있는 대화를 모두 썼어요." + when
        return HTTPException(status.HTTP_402_PAYMENT_REQUIRED, text)
    if ent.status == "TRIAL":
        text = (
            "무료 체험에서 쓸 수 있는 대화를 모두 썼어요. 요금제를 고르면 이어서 대화할 수 있어요."
        )
    elif ent.status == "PRELAUNCH":
        made = f"({_date(ent.made_at)})" if ent.made_at else ""
        text = (
            "결제 전 무료로 쓸 수 있는 대화를 모두 썼어요"
            f"(대화 상대를 만든 날{made}부터 30일마다 채워져요)."
        )
        if ent.resets_at is not None:
            text += f" {_date(ent.resets_at)}에 다시 채워져요."
    else:
        text = "이번 달에 쓸 수 있는 대화를 모두 썼어요."
        if ent.status == "PAST_DUE":
            text += " 결제가 되면 다시 채워져요."
        elif ent.resets_at is not None:
            text += f" {_date(ent.resets_at)}에 다시 채워져요."
        if ent.plan != billing.TOP_PLAN:
            text += " 요금제를 올리면 바로 더 대화할 수 있어요."
    return HTTPException(status.HTTP_402_PAYMENT_REQUIRED, text)


async def start_conversation(db, user, access: sharing.ChatAccess):
    """대화 횟수가 남았는지 보고 1번을 잡아 둔다.

    다 썼으면 끊지 않고 글로만 대답한다(마스터 리포트: 하드캡 금지). 그 글 대답에도 한 달 공정 사용
    상한이 있고, 넘으면 402 와 언제 다시 채워지는지 알려 준다.
    돌려주는 (권리, 잡은 대화, 글로만) 가운데 잡은 대화는 대답을 받지 못하면
    billing.release_conversation 으로 돌려준다(실패한 대화는 세지 않는다).
    """
    persona = access.persona
    ent = await require_active(db, access)
    left = ent.conversations_left
    if left is None or left > 0:
        reserved = await billing.reserve_conversation(db, user, persona, ent)
        if reserved is not None:
            return ent, reserved, False
        # 동시에 보낸 다른 대화가 마지막 횟수를 가져갔다 — 이번 것은 글로 대답한다
    reserved = await billing.reserve_conversation(
        db, user, persona, ent, kind="text_only", limit=get_settings().text_only_fair_use
    )
    if reserved is None:
        raise _exhausted(ent, member=access.member)
    return ent, reserved, True


def _shared(access: sharing.ChatAccess) -> dict:
    """함께 쓰는 가족의 대화·듣기 통계에만 shared=true 를 붙인다(주인 것은 예전과 같다)."""
    return {"shared": True} if access.member else {}


def _left_after(ent: billing.Entitlement) -> int | None:
    left = ent.conversations_left
    return None if left is None else max(0, left - 1)


async def _note_first_chat(access: sharing.ChatAccess, user_id: str) -> None:
    """결제 후 첫 대화를 결제 기록에 남긴다(청약철회 판단 — 초대한 가족의 대화도 센다). 대답을
    받아 저장한 뒤에만 부른다. billing 이 따로 연 세션으로 쓰고, 실패해도 대답은 그대로 준다."""
    try:
        await billing.note_first_chat(
            persona_id=access.persona.id,
            user_id=user_id,
            member=access.member,
            at=datetime.now(UTC),
        )
    except Exception:  # noqa: BLE001 — 대화는 이미 끝났다(기록만 놓친다)
        logger.exception("first chat after payment was not recorded")


@router.post("/{conversation_id}/messages/text", response_model=Envelope[MessageExchange])
async def text_message(
    conversation_id: str,
    payload: TextMessageCreate,
    user: CurrentUser,
    db: DB,
    background: BackgroundTasks,
):
    deadline = monotonic() + EXCHANGE_BUDGET_SECONDS
    ratelimit.MESSAGES.check(user.id)
    conversation = await owned_conversation(db, conversation_id, user.id)
    # 방이 내 것이어도 대화 상대에 들어갈 수 있는지 다시 본다(내보내진 가족은 404)
    access = await sharing.chat_access(db, conversation.persona_id, user)
    persona = access.persona
    # 멈춘 대화 상대(이의 접수 등)는 대화하지 않는다(만든 분 409, 가족 403 share_paused)
    await gate.require(db, persona, "chat", member=access.member)
    ent, reserved, text_only = await start_conversation(db, user, access)
    try:
        result = await exchange(
            db,
            conversation,
            payload.content,
            text_only=text_only,
            access=access,
            deadline=deadline,
            background=background,
        )
    except BaseException:
        await db.rollback()
        await billing.release_conversation(db, reserved)
        raise
    await _note_first_chat(access, user.id)
    result.conversations_left = 0 if text_only else _left_after(ent)
    result.text_only = text_only
    await analytics.track(
        db,
        "message_sent",
        user_id=user.id,
        persona_id=persona.id,
        kind="text",
        textOnly=text_only,
        **_shared(access),
    )
    return Envelope(data=result)


def _upload_size(upload: UploadFile) -> int:
    """받아 둔 파일 크기(읽지 않고 잰다)."""
    handle = upload.file
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(0)
    return size


def _voice_too_long(seconds: int) -> HTTPException:
    limit = f"{seconds // 60}분" if seconds % 60 == 0 else f"{seconds}초"
    return HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE,
        f"녹음이 너무 길어요. {limit} 안으로 나눠서 보내 주세요.",
    )


async def _drop_file(key: str) -> None:
    try:
        await asyncio.to_thread(get_storage().delete_prefix, key)
    except Exception as error:  # noqa: BLE001 — 남은 파일은 대화방을 지울 때 함께 지운다
        logger.warning("audio cleanup failed: %s", type(error).__name__)


@router.post("/{conversation_id}/messages/voice", response_model=Envelope[MessageExchange])
async def voice_message(
    conversation_id: str,
    user: CurrentUser,
    db: DB,
    file: Annotated[UploadFile, File()],
    background: BackgroundTasks,
):
    """말로 보내기. 글로 옮기기(돈이 든다) 전에 크기·횟수·길이를 본다.

    한 사람은 한 번에 하나만 보내고(앞의 대답을 받은 뒤에), 서버 전체에서 동시에 글로 옮기는 수도
    STT_SLOTS 개로 막는다. 녹음은 대답을 받기 전에 저장하고, 대답을 받지 못하면 지운다.
    """
    deadline = monotonic() + EXCHANGE_BUDGET_SECONDS
    ratelimit.MESSAGES.check(user.id)
    conversation = await owned_conversation(db, conversation_id, user.id)
    access = await sharing.chat_access(db, conversation.persona_id, user)
    await gate.require(db, access.persona, "chat", member=access.member)
    size = _upload_size(file)
    if size > VOICE_MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "녹음이 너무 길어요. 조금 짧게 나눠서 보내 주세요.",
        )
    if not size:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "녹음된 소리가 없어요.")
    # 실패하면 되돌리며(rollback) 줄 값이 비워진다. id 는 미리 잡아 둔다.
    user_id = user.id
    if user_id in _voice_senders:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, VOICE_BUSY_USER)
    _voice_senders.add(user_id)
    try:
        return await _voice_exchange(db, user, conversation, access, file, deadline, background)
    finally:
        _voice_senders.discard(user_id)


async def _voice_exchange(
    db: DB,
    user,
    conversation: Conversation,
    access: sharing.ChatAccess,
    file: UploadFile,
    deadline: float,
    background: BackgroundTasks | None = None,
) -> Envelope[MessageExchange]:
    global _stt_active
    # 말소리를 글로 바꾸기(돈이 든다) 전에 횟수부터 본다
    persona = access.persona
    ent, reserved, text_only = await start_conversation(db, user, access)
    if text_only:
        # 횟수를 다 쓴 뒤에는 글로만 대화한다(말을 글로 옮기는 데도 돈이 든다)
        await billing.release_conversation(db, reserved)
        raise HTTPException(
            status.HTTP_409_CONFLICT, MEMBER_TEXT_ONLY if access.member else OWNER_TEXT_ONLY
        )
    if _stt_active >= STT_SLOTS:
        await billing.release_conversation(db, reserved)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, VOICE_BUSY)
    # 확장자·Content-Type 은 보낸 값을 믿지 않고 아는 소리 형식으로만 저장한다
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in audio.AUDIO_SUFFIXES:
        suffix = ".webm"
    audio_url: str | None = None
    _stt_active += 1
    try:
        # 글로 옮기는 동안 연결을 트랜잭션 속에 붙잡아 두지 않는다
        await db.commit()
        content = await file.read()
        cap = get_settings().max_voice_message_seconds
        seconds = await asyncio.to_thread(audio.voice_seconds, content, suffix, cap)
        if seconds is not None and seconds > cap:
            raise _voice_too_long(cap)
        stt_text = await ai_service.transcribe(
            file.filename or "voice.webm", content, file.content_type or "application/octet-stream"
        )
        if not (stt_text or "").strip():
            # 알아듣지 못한 녹음은 대화로 세지 않는다
            raise HTTPException(422, "말씀을 알아듣지 못했어요. 다시 말해 주세요.")
        if len(stt_text) > STT_MAX_CHARS:
            raise _voice_too_long(cap)
        key = f"conversations/{conversation.id}/voice/{uuid4()}{suffix}"
        await asyncio.to_thread(get_storage().put, key, content, audio.AUDIO_CONTENT_TYPES[suffix])
        audio_url = key
        del content
        result = await exchange(
            db,
            conversation,
            stt_text,
            audio_url,
            access=access,
            deadline=deadline,
            background=background,
        )
    except BaseException:
        await db.rollback()
        await billing.release_conversation(db, reserved)
        if audio_url:
            # 대답을 받지 못한 말 녹음은 남기지 않는다(대화방을 지운 뒤에 저장됐을 수도 있다)
            await _drop_file(audio_url)
        raise
    finally:
        _stt_active -= 1
    await _note_first_chat(access, user.id)
    result.stt_text = stt_text
    result.conversations_left = _left_after(ent)
    await analytics.track(
        db,
        "message_sent",
        user_id=user.id,
        persona_id=persona.id,
        kind="voice",
        textOnly=False,
        **_shared(access),
    )
    return Envelope(data=result)


@speech_router.post("/messages/{message_id}/speech", response_model=Envelope[dict])
async def message_speech(message_id: str, user: CurrentUser, db: DB):
    """페르소나 대답을 그분 목소리로. 이 페르소나 자신의 목소리가 있을 때만 만든다.

    AI 서버는 voiceId 가 없으면 ELEVENLABS_DEFAULT_VOICE_ID 로 폴백하는데, 그 값은 특정
    대상자의 복제 목소리다. 다른 페르소나가 그 목소리로 말하면 안 되어 여기서 막는다.
    """
    ratelimit.SPEECH.check(user.id)
    message = await db.get(Message, message_id)
    conversation = await db.get(Conversation, message.conversation_id) if message else None
    if message is None or conversation.user_id != user.id or message.role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "메시지를 찾지 못했어요.")
    access = await sharing.chat_access(db, conversation.persona_id, user)
    persona = access.persona
    await gate.require(db, persona, "speech", member=access.member)
    if not persona.voice_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "아직 그분 목소리가 없어요.")
    if message.text_only and not message.audio_url:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "대화 횟수를 다 쓴 뒤의 대답이라 글로만 드렸어요. "
            "다음 결제일부터 다시 목소리로 들려 드려요.",
        )
    fresh = not message.audio_url
    if not message.audio_url:
        # 대답 목소리는 대화 횟수(보낼 때 센다)에 들어 있어 따로 세지 않는다.
        # 구독이 끝난 뒤 예전 대답을 새로 읽게 하지는 않는다(목소리 만들기에 돈이 든다).
        if access.member and not sharing.member_voice_ok(persona):
            # 함께 쓰는 가족에게는 Fish 목소리로만 읽어 준다(sharing.member_voice_ok). 예전
            # 목소리(ElevenLabs·MiniMax)로는 새로 읽지 않는다(저장된 소리는 듣는다)
            raise sharing.fail("share_voice_unavailable")
        await require_active(db, access)
        pending = _speaking.get(message.id)
        if pending is not None:
            # 같은 대답을 이미 목소리로 만드는 중이다(자동 재생과 '듣기'를 함께 누름). 목소리
            # 회사에 두 번 보내지 않고, 끝나길 기다려 그 소리를 준다.
            await _wait_for_speech(db, message, pending)
        else:
            await _make_speech(db, conversation, message, persona)
    await analytics.track(
        db, "reply_played", user_id=user.id, persona_id=persona.id, fresh=fresh, **_shared(access)
    )
    return Envelope(data={"audioUrl": resolve_audio_url(message.audio_url)})


async def _wait_for_speech(db: DB, message: Message, pending: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(pending.wait(), SPEECH_WAIT_SECONDS)
    except TimeoutError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, SPEECH_FAILED) from error
    await db.refresh(message)
    if not message.audio_url:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, SPEECH_FAILED)


async def _make_speech(
    db: DB, conversation: Conversation, message: Message, persona: Persona
) -> None:
    """대답을 그분 목소리로 만들어 저장한다(message.audio_url). 같은 대답은 하나만 만든다."""
    # 되돌리면(rollback) 줄 값을 다시 읽어야 해서 id 는 미리 잡아 둔다
    message_id = message.id
    done = _speaking[message_id] = asyncio.Event()
    try:
        # 목소리로 읽을 글(AI speechText, 연기 표시 포함)이 있으면 그것을, 없으면 대답 글을 읽는다
        text = message.speech_text or message.content
        voice_id, emotion = persona.voice_id, message.emotion
        # 목소리 회사를 기다리는 동안 읽던 트랜잭션을 닫아 둔다(그사이 바뀐 목소리를 다시 읽게)
        await db.commit()
        try:
            speech = await ai_service.speech(text=text, voice_id=voice_id, emotion=emotion)
        except AIServerError as error:
            # 원문은 ai 가 로그로 남겼다. 고객에게는 차분한 한 문장만.
            logger.warning("speech failed for message %s: %s", message_id, error)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, SPEECH_FAILED) from error
        # 저장소에 먼저 올린다(키는 목소리마다 정해져 있다). 대화 상대 줄을 잠근 채 올리면 그동안
        # 이 대화 상대의 대화 보내기(대화 횟수 잡기)가 기다린다.
        key = persona_builder.reply_audio_key(conversation.id, message_id, voice_id)
        await asyncio.to_thread(get_storage().put, key, speech, "audio/mpeg")
        # 만드는 사이 목소리를 다시 만들었으면(바꿀 때 대답 음성을 비운다) 예전 목소리 소리를
        # 남기지 않는다. 남기면 그 대답만 예전 목소리로 남는다. 확인부터 저장까지 대화 상대
        # 줄을 잠가, 그사이 바꾸기(persona_builder.run_voice — 이 줄을 고친다)가 끼어들지 못하게
        # 한다. 바꾸기가 먼저 잡았으면 끝날 때까지 기다렸다가 바뀐 목소리를 읽는다.
        current = await db.scalar(
            select(Persona.voice_id).where(Persona.id == persona.id).with_for_update()
        )
        if current != voice_id:
            await db.rollback()
            await _drop_file(key)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "그분 목소리를 방금 새로 만들었어요. 다시 들어 주세요.",
            )
        # 그사이 대화방이 지워졌으면(가족이 나감·내보냄) 저장하지 않는다(지운 대화의 소리가
        # 저장소에 남지 않게). 메시지 줄을 잠가 지우기와 엇갈리지 않게 한다.
        still = await db.scalar(
            select(Message.id).where(Message.id == message_id).with_for_update()
        )
        if still is None:
            await db.rollback()
            await _drop_file(key)
            raise HTTPException(status.HTTP_404_NOT_FOUND, "메시지를 찾지 못했어요.")
        message.audio_url = key
        await db.commit()
    finally:
        _speaking.pop(message_id, None)
        done.set()
