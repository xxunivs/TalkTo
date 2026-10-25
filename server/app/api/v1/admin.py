from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import String, cast, func, or_, select

from app.api.deps import DB, AdminUser
from app.api.v1.auth import user_views
from app.core.security import hash_password, run_hashing
from app.db.models import (
    Conversation,
    ConversationFeedback,
    ErrorLog,
    Feedback,
    Memory,
    Message,
    Person,
    Persona,
    SocialAccount,
    User,
    UserStatus,
)
from app.schemas.api import (
    AdminConversationFeedbackView,
    AdminFeedbackView,
    AdminUserView,
    DailyMetric,
    DashboardOverview,
    FeedbackView,
    InvitationCreate,
    ReviewedUpdate,
    UserUpdate,
    UserView,
)
from app.schemas.common import Envelope, Meta
from app.services import notify, persona_import

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/notify-test", response_model=Envelope[dict])
async def notify_test(_: AdminUser):
    """디스코드 알림 시험(운영에서 웹후크 주소가 있을 때만 실제로 간다)."""
    configured = notify.configured()
    notify.send("✅ TalkTo 알림이 연결됐어요. 새 가입·구독 신청·오류를 이 채널로 알려 드릴게요.")
    notify.send("✅ TalkTo 오류·문제 알림 채널이에요.", channel="alerts")
    return Envelope(data={"configured": configured})


def _like(term: str) -> str:
    """ILIKE 에 넣을 '%…%'. 사용자가 친 %·_ 는 글자 그대로 찾는다."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/users", response_model=Envelope[list[AdminUserView]])
async def list_users(
    _: AdminUser,
    db: DB,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    q: str | None = Query(None, max_length=100),
):
    """사용자 목록(새로 가입한 순). q 가 있으면 이메일·이름·소셜 계정 이메일·id 로 찾는다.

    카카오가 이메일을 주지 않은 계정은 users.email 이 자리표시 주소라 소셜 이메일로도 찾는다.
    줄마다 이은 소셜 전부(providers)·대화 상대 수·가린 이메일·가져온 대화 상대를 붙인다(가져오기
    대상 고르기·되돌리기). 전체 이메일은 싣지 않는다.
    """
    query = select(User)
    term = (q or "").strip()
    if term:
        like = _like(term)
        social = select(SocialAccount.user_id).where(SocialAccount.email.ilike(like, escape="\\"))
        query = query.where(
            or_(
                User.id == term,
                User.email.ilike(like, escape="\\"),
                User.name.ilike(like, escape="\\"),
                User.id.in_(social),
            )
        )
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    users = list(
        (
            await db.scalars(
                query.order_by(User.created_at.desc()).offset((page - 1) * limit).limit(limit)
            )
        ).all()
    )
    info = await persona_import.account_info(db, users)
    data = [
        AdminUserView.model_validate(
            {
                "id": user.id,
                "name": user.name,
                "role": user.role,
                "status": user.status,
                "createdAt": user.created_at,
                # providers 는 이은 순서라 첫째가 처음 이은 소셜이다(user_views 와 같다)
                "provider": (info[user.id]["providers"] or [None])[0],
                **info[user.id],
            }
        )
        for user in users
    ]
    return Envelope(data=data, meta=Meta(page=page, limit=limit, total=total))


@router.post("/users/invitations", response_model=Envelope[dict], status_code=201)
async def invite_user(payload: InvitationCreate, _: AdminUser, db: DB):
    if await db.scalar(select(User.id).where(User.email == payload.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email is already registered")
    temporary_password = token_urlsafe(12)
    user = User(
        email=payload.email.lower(),
        name=payload.name,
        role=payload.role,
        status=UserStatus.INVITED,
        password_hash=await run_hashing(hash_password, temporary_password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return Envelope(
        data={"user": UserView.model_validate(user), "temporaryPassword": temporary_password}
    )


@router.patch("/users/{user_id}", response_model=Envelope[UserView])
async def update_user(user_id: str, payload: UserUpdate, _: AdminUser, db: DB):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    await db.commit()
    await db.refresh(user)
    return Envelope(data=(await user_views(db, [user]))[0])


@router.get("/personas/{persona_id}/artifacts", response_model=Envelope[dict])
async def persona_artifacts(persona_id: str, _: AdminUser, db: DB):
    """대화에 들어가는 지침과 기억(관리자만). 왜 그렇게 말하는지 확인할 때 본다.

    고객에게는 보이지 않는다(지침은 AI 에게 주는 지시문이라 고객이 볼 글이 아니다).
    """
    persona = await db.get(Persona, persona_id)
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "대화 상대를 찾지 못했어요.")
    memories = (
        await db.scalars(
            select(Memory)
            .where(Memory.person_id == persona.person_id, Memory.status == "ACTIVE")
            .order_by(Memory.created_at)
        )
    ).all()
    # 성함은 보이지 않는다(내 할머니를 이름으로 부르지 않는다). 지침·기억에 든 성함은
    # 호칭으로 바꿔 보여준다. 대화에 쓰는 실제 지침은 그대로다.
    basics = persona.basics or {}
    name = (basics.get("name") or persona.name or "").strip()
    shown = (basics.get("addressTerm") or basics.get("relation") or "그분").strip()

    def mask(text: str | None) -> str:
        return text.replace(name, shown) if name and text else (text or "")

    return Envelope(
        data={
            "instructions": mask(persona.instructions),
            "memories": [
                {
                    "id": m.id,
                    "text": mask(m.body_markdown),
                    "source": m.source,
                    # 가족이 '내용이 틀려요'라고 한 대답에 쓰인 기억(끌 후보, 저절로 숨기지 않는다)
                    "flaggedWrong": bool(m.flagged_wrong),
                }
                for m in memories
            ],
        }
    )


@router.get("/metrics/overview", response_model=Envelope[DashboardOverview])
async def overview(_: AdminUser, db: DB):
    async def count(model, *criteria):
        return await db.scalar(select(func.count()).select_from(model).where(*criteria)) or 0

    return Envelope(
        data=DashboardOverview(
            total_users=await count(User),
            active_users=await count(User, User.status == "ACTIVE"),
            total_conversations=await count(Conversation),
            total_messages=await count(Message),
            total_memories=await count(Memory),
            negative_feedback=await count(Feedback, Feedback.rating == "DOWN"),
        )
    )


@router.get("/metrics/daily", response_model=Envelope[list[DailyMetric]])
async def daily_metrics(_: AdminUser, db: DB, days: int = Query(14, ge=1, le=90)):
    start = datetime.now(UTC) - timedelta(days=days - 1)

    async def grouped(model):
        timestamps = (
            await db.scalars(select(model.created_at).where(model.created_at >= start))
        ).all()
        counts: dict[str, int] = {}
        for timestamp in timestamps:
            day = timestamp.date().isoformat()
            counts[day] = counts.get(day, 0) + 1
        return counts

    conversations, messages, feedback = (
        await grouped(Conversation),
        await grouped(Message),
        await grouped(Feedback),
    )
    data = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date().isoformat()
        data.append(
            DailyMetric(
                date=day,
                conversations=conversations.get(day, 0),
                messages=messages.get(day, 0),
                feedback=feedback.get(day, 0),
            )
        )
    return Envelope(data=data)


@router.get("/logs/errors", response_model=Envelope[list[dict]])
async def error_logs(_: AdminUser, db: DB, limit: int = Query(50, le=200)):
    logs = (
        await db.scalars(select(ErrorLog).order_by(ErrorLog.created_at.desc()).limit(limit))
    ).all()
    return Envelope(
        data=[
            {
                "id": x.id,
                "level": x.level,
                "path": x.path,
                "message": x.message,
                "detail": x.detail,
                "createdAt": x.created_at,
            }
            for x in logs
        ]
    )


def _persona_title(persona: Persona | None) -> str | None:
    """관리자 목록에 보일 대화 상대 이름. 성함 대신 호칭·관계(그분을 이름으로 부르지 않는다)."""
    if persona is None:
        return None
    basics = persona.basics or {}
    return basics.get("addressTerm") or basics.get("relation") or "그분"


async def _owners(db: DB, persona_ids: set[str]) -> dict[str, tuple[Persona, str]]:
    """대화 상대 id → (대화 상대, 만든 분 id)."""
    if not persona_ids:
        return {}
    rows = (
        await db.execute(
            select(Persona, Person.owner_user_id)
            .join(Person, Person.id == Persona.person_id)
            .where(Persona.id.in_(persona_ids))
        )
    ).all()
    return {persona.id: (persona, owner) for persona, owner in rows}


@router.get("/feedback", response_model=Envelope[list[AdminFeedbackView]])
async def feedback_list(
    _: AdminUser,
    db: DB,
    rating: str | None = None,
    tag: str | None = Query(None, max_length=40),
    reviewed: bool | None = None,
    state: str | None = Query(None, max_length=20),
    corrected: bool | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """대답 평가 목록(최근 것부터).

    tag(LIKE·STYLE·CONTENT·TOO_LONG·VOICE…)·reviewed·state·고침 있음(corrected)으로 거른다.
    줄마다 평가한 대답 글과 대화 상대(호칭)를 붙인다. meta.total 은 거른 뒤 전체 수."""
    query = (
        select(Feedback, Message.content)
        .join(Message, Message.id == Feedback.message_id)
        .order_by(Feedback.created_at.desc())
    )
    if rating:
        query = query.where(Feedback.rating == rating)
    if reviewed is not None:
        query = query.where(Feedback.reviewed.is_(reviewed))
    if state:
        query = query.where(Feedback.state == state)
    if corrected is not None:
        query = query.where(
            Feedback.correction.is_not(None) if corrected else Feedback.correction.is_(None)
        )
    if tag:
        # 태그는 JSON 목록(["STYLE", "CONTENT"])이다. 글로 바꿔 '"태그"'가 든 줄만 DB 에서 거른다
        # (SQLite·Postgres 모두 JSON 을 그 글 그대로 돌려준다). 평가가 늘어도 한 쪽만 읽는다.
        needle = tag.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(cast(Feedback.tags, String).like(f'%"{needle}"%', escape="\\"))
    total = int(
        await db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    )
    rows = list((await db.execute(query.offset((page - 1) * limit).limit(limit))).all())
    owners = await _owners(db, {item.persona_id for item, _ in rows if item.persona_id})
    data = []
    for item, content in rows:
        persona, owner = owners.get(item.persona_id, (None, None))
        data.append(
            AdminFeedbackView.model_validate(item).model_copy(
                update={
                    "reply": content,
                    "persona_title": _persona_title(persona),
                    "member": bool(owner and owner != item.user_id),
                }
            )
        )
    return Envelope(data=data, meta=Meta(page=page, limit=limit, total=total))


@router.patch("/feedback/{feedback_id}", response_model=Envelope[AdminFeedbackView])
async def review_feedback(feedback_id: str, payload: ReviewedUpdate, _: AdminUser, db: DB):
    """확인함 켜고 끄기."""
    item = await db.get(Feedback, feedback_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "평가를 찾지 못했어요.")
    item.reviewed = payload.reviewed
    await db.commit()
    await db.refresh(item)
    return Envelope(data=AdminFeedbackView.model_validate(item))


@router.get(
    "/conversation-feedback", response_model=Envelope[list[AdminConversationFeedbackView]]
)
async def conversation_feedback_list(
    _: AdminUser,
    db: DB,
    reviewed: bool | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """대화 전체에 남긴 한 줄 목록(최근 것부터)."""
    query = select(ConversationFeedback)
    if reviewed is not None:
        query = query.where(ConversationFeedback.reviewed.is_(reviewed))
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        (
            await db.scalars(
                query.order_by(ConversationFeedback.created_at.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        ).all()
    )
    owners = await _owners(db, {row.persona_id for row in rows if row.persona_id})
    data = []
    for row in rows:
        persona, owner = owners.get(row.persona_id, (None, None))
        data.append(
            AdminConversationFeedbackView.model_validate(row).model_copy(
                update={
                    "persona_title": _persona_title(persona),
                    "member": bool(owner and owner != row.user_id),
                }
            )
        )
    return Envelope(data=data, meta=Meta(page=page, limit=limit, total=total))


@router.patch(
    "/conversation-feedback/{item_id}", response_model=Envelope[AdminConversationFeedbackView]
)
async def review_conversation_feedback(
    item_id: str, payload: ReviewedUpdate, _: AdminUser, db: DB
):
    item = await db.get(ConversationFeedback, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "남긴 말을 찾지 못했어요.")
    item.reviewed = payload.reviewed
    await db.commit()
    await db.refresh(item)
    return Envelope(data=AdminConversationFeedbackView.model_validate(item))


@router.get("/feedback/negative-summary", response_model=Envelope[list[dict]])
async def negative_summary(_: AdminUser, db: DB):
    rows = (await db.scalars(select(Feedback).where(Feedback.rating == "DOWN"))).all()
    counts: dict[str, int] = {}
    for item in rows:
        for tag in item.tags:
            counts[tag] = counts.get(tag, 0) + 1
    return Envelope(
        data=[
            {"tag": tag, "count": count}
            for tag, count in sorted(counts.items(), key=lambda x: -x[1])
        ]
    )


@router.get("/feedback/reviews", response_model=Envelope[list[FeedbackView]])
async def feedback_reviews(_: AdminUser, db: DB):
    rows = (
        await db.scalars(
            select(Feedback).where(Feedback.reviewed.is_(False)).order_by(Feedback.created_at)
        )
    ).all()
    return Envelope(data=[FeedbackView.model_validate(x) for x in rows])
