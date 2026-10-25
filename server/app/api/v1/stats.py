"""개발자 화면 · 사용 통계(관리자만). GET /admin/stats, GET /admin/stats/export.

- 관리자 계정의 사용은 세지 않는다(시험해 본 것이 섞이지 않게).
- 탈퇴한 계정은 '탈퇴' 수로만 따로 세고, 최근 목록에는 넣지 않는다.
- 개수는 SQL 로 세고, 날짜별·화면별처럼 DB 마다 문법이 다른 것은 작은 값만 읽어 여기서 센다
  (수천 줄이면 충분히 빠르다).
- 사람별 진행 단계(users·userStages)는 기간과 상관없이 가입자 전부의 지금 상태를 본다.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query, Response
from sqlalchemy import and_, distinct, func, or_, select

from app.api.deps import DB, AdminUser
from app.api.v1.billing import persona_relation, persona_title
from app.db.models import (
    SOCIAL_PLACEHOLDER_EMAIL_SUFFIX,
    AnalyticsEvent,
    Consent,
    Conversation,
    ErrorLog,
    Feedback,
    Message,
    Person,
    Persona,
    PersonaMaterial,
    PersonaShare,
    Recording,
    SetupStep,
    SocialAccount,
    Subscription,
    SubscriptionRequest,
    User,
    UserRole,
    UserStatus,
)
from app.schemas.common import Envelope
from app.services import ai, billing, legal

router = APIRouter(prefix="/admin", tags=["Admin"])

KST = billing.KST

FUNNEL = [
    ("signed_up", "가입"),
    ("consented", "동의 완료"),
    ("persona_created", "대화 상대 만들기 시작"),
    ("recordings_uploaded", "녹음 올림"),
    ("build_done", "다 만듦"),
    ("first_message", "첫 대화"),
    ("engaged", "대화 10번 이상"),
    ("subscription_requested", "구독 신청"),
]
ENGAGED_MESSAGES = 10
STEPS = [s.value for s in SetupStep]
LIFE_KEYS = ("deceased", "alive", "unable")
# fish = 지금 목소리. minimax·elevenlabs·elevenlabs_pvc 는 Fish 로 다시 만들기 전의 예전 목소리
# (다시 만들기가 얼마나 남았는지 본다). 모르는 값은 그대로 더한다.
VOICE_KEYS = ("fish", "minimax", "elevenlabs", "elevenlabs_pvc", "none")
REQUEST_PLANS = ("memories", "family")
# 사람마다 지금 어느 단계인가(개발자 화면 '사람별 진행 단계'). 차례대로 — 대화 상대가 여럿이면 가장
# 멀리 간 것을 보고, 뒤 단계에 온 사람은 앞 단계도 거친 것으로 센다.
USER_STAGES = [
    ("signed_up", "가입만"),
    ("consented", "동의 완료"),
    ("persona_started", "만들기 시작"),
    ("materials", "녹음·자료 올림"),
    ("voice", "목소리 고름"),
    ("building", "만드는 중"),
    ("ready", "완성"),
    ("chatting", "대화 중"),
    ("requested", "구독 신청"),
    ("subscribed", "결제함"),
]
# 갈래: 제 대화 상대는 없이(동의까지만) 가족이 나눠 준 대화 상대에 들어온 사람
FAMILY_STAGE = ("family_member", "가족 초대로 들어옴")
STAGE_RANK = {key: i for i, (key, _) in enumerate(USER_STAGES)}
# UPLOAD 는 녹음·글 자료가 있는지로 나눈다(persona_started / materials)
STEP_STAGE = {
    SetupStep.SPEAKERS: "materials",  # 옮겨 적기가 끝나 목소리를 고르는 중
    SetupStep.QUESTIONS: "voice",  # 목소리를 골랐고 질문에 답하는 중
    SetupStep.BUILDING: "building",
    SetupStep.READY: "ready",
}
# 한 사람의 구독 신청이 여럿이면 결제함 > 신청함 > 취소함
REQUEST_ORDER = {"CANCELED": 0, billing.REQUESTED: 1, billing.CONVERTED: 2}


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _kst_date(value: datetime) -> str:
    return _aware(value).astimezone(KST).date().isoformat()


def _latest(*values: datetime | None) -> datetime | None:
    seen = [_aware(v) for v in values if v is not None]
    return max(seen) if seen else None


def _contact_email(email: str | None) -> str | None:
    """연락할 수 있는 이메일만(소셜 로그인이 이메일을 주지 않았으면 None)."""
    if not email or email.endswith(".invalid"):
        return None
    return email


# ────────────────────────── 걸러 내기(관리자·탈퇴) ──────────────────────────


def _admins():
    return select(User.id).where(User.role == UserRole.ADMIN)


def _customer_users():
    """관리자가 아닌 사용자(탈퇴 포함)."""
    return User.role != UserRole.ADMIN


def _live_customer():
    return and_(
        User.role != UserRole.ADMIN,
        User.status != UserStatus.DELETED,
        ~User.email.startswith("deleted-"),
    )


def _events(*criteria):
    """관리자 이벤트를 뺀 이벤트(탈퇴해 user_id 가 빈 것은 센다)."""
    return select(AnalyticsEvent).where(
        or_(AnalyticsEvent.user_id.is_(None), AnalyticsEvent.user_id.not_in(_admins())),
        *criteria,
    )


def _customer_personas():
    return Person.owner_user_id.not_in(_admins())


async def _count(db, query) -> int:
    return int(await db.scalar(query) or 0)


async def _ids(db, query) -> set[str]:
    return {row for row in (await db.scalars(query)).all() if row}


# ────────────────────────── 통계 ──────────────────────────


async def _totals(db, current: datetime) -> dict[str, int]:
    user_messages = (
        select(func.count(Message.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.role == "user", Conversation.user_id.not_in(_admins()))
    )
    personas = (
        select(func.count(Persona.id))
        .join(Person, Person.id == Persona.person_id)
        .where(_customer_personas())
    )
    feedback = select(func.count(Feedback.id)).where(Feedback.user_id.not_in(_admins()))
    return {
        "users": await _count(
            db,
            select(func.count(User.id)).where(_customer_users(), User.status != UserStatus.DELETED),
        ),
        "deletedUsers": await _count(
            db,
            select(func.count(User.id)).where(_customer_users(), User.status == UserStatus.DELETED),
        ),
        "personas": await _count(db, personas),
        "personasReady": await _count(db, personas.where(Persona.setup_step == SetupStep.READY)),
        "conversations": await _count(
            db,
            select(func.count(Conversation.id)).where(Conversation.user_id.not_in(_admins())),
        ),
        "messages": await _count(db, user_messages),
        "voiceMessages": await _count(db, user_messages.where(Message.audio_url.is_not(None))),
        "replyPlays": await _count(
            db,
            select(func.count()).select_from(
                _events(AnalyticsEvent.name == "reply_played").subquery()
            ),
        ),
        "subscriptionRequests": await _count(
            db,
            select(func.count(SubscriptionRequest.id)).where(
                SubscriptionRequest.status == billing.REQUESTED,
                SubscriptionRequest.user_id.not_in(_admins()),
            ),
        ),
        "feedbackUp": await _count(db, feedback.where(Feedback.rating == "UP")),
        "feedbackDown": await _count(db, feedback.where(Feedback.rating == "DOWN")),
        "errors24h": await _count(
            db,
            select(func.count(ErrorLog.id)).where(
                ErrorLog.created_at >= current - timedelta(hours=24)
            ),
        ),
    }


async def _active_users(db, since: datetime) -> set[str]:
    """그때부터 이벤트나 메시지가 있는 서로 다른 사용자."""
    from_events = await _ids(
        db,
        select(AnalyticsEvent.user_id)
        .distinct()
        .where(
            AnalyticsEvent.user_id.is_not(None),
            AnalyticsEvent.user_id.not_in(_admins()),
            AnalyticsEvent.created_at >= since,
        ),
    )
    from_messages = await _ids(
        db,
        select(Conversation.user_id)
        .distinct()
        .join(Message, Message.conversation_id == Conversation.id)
        .where(
            Message.role == "user",
            Message.created_at >= since,
            Conversation.user_id.not_in(_admins()),
        ),
    )
    return from_events | from_messages


async def _event_users(db, name: str, cohort) -> set[str]:
    return await _ids(
        db,
        select(AnalyticsEvent.user_id)
        .distinct()
        .where(AnalyticsEvent.name == name, AnalyticsEvent.user_id.in_(cohort)),
    )


async def _funnel(db, start: datetime) -> list[dict[str, Any]]:
    """그 기간에 가입한 사람(관리자·탈퇴 제외)이 각 단계까지 왔는지. 표와 이벤트를 함께 본다
    (통계를 모으기 전부터 있던 사람·지운 대화 상대도 빠지지 않게)."""
    cohort = select(User.id).where(_live_customer(), User.created_at >= start)
    signed = await _ids(db, cohort)
    owners = (
        select(Person.owner_user_id)
        .distinct()
        .join(Persona, Persona.person_id == Person.id)
        .where(Person.owner_user_id.in_(cohort))
    )
    required = len(legal.ACCOUNT_TYPES)
    consented = await _ids(
        db,
        select(Consent.user_id)
        .where(
            Consent.user_id.in_(cohort),
            Consent.consent_type.in_(legal.ACCOUNT_TYPES),
            Consent.agreed.is_(True),
        )
        .group_by(Consent.user_id)
        .having(func.count(distinct(Consent.consent_type)) >= required),
    ) | await _event_users(db, "consent_accepted", cohort)
    created = await _ids(db, owners) | await _event_users(db, "persona_created", cohort)
    uploaded = await _ids(
        db, owners.join(Recording, Recording.persona_id == Persona.id)
    ) | await _event_users(db, "recordings_uploaded", cohort)
    built = await _ids(
        db, owners.where(Persona.setup_step == SetupStep.READY)
    ) | await _event_users(db, "build_done", cohort)
    message_counts = (
        select(Conversation.user_id, func.count(Message.id))
        .join(Message, Message.conversation_id == Conversation.id)
        .where(Message.role == "user", Conversation.user_id.in_(cohort))
        .group_by(Conversation.user_id)
    )
    counts = {uid: int(n) for uid, n in (await db.execute(message_counts)).all()}
    event_counts = {
        uid: int(n)
        for uid, n in (
            await db.execute(
                select(AnalyticsEvent.user_id, func.count(AnalyticsEvent.id))
                .where(AnalyticsEvent.name == "message_sent", AnalyticsEvent.user_id.in_(cohort))
                .group_by(AnalyticsEvent.user_id)
            )
        ).all()
    }
    first = {u for u, n in counts.items() if n > 0} | {u for u, n in event_counts.items() if n}
    # 가족이 나눠 준 대화 상대와만 이야기한 사람은 만들기 깔때기에 넣지 않는다(뒤 단계에 온 사람을
    # 앞 단계도 거친 것으로 세서 '만들기 시작·녹음·다 만듦'이 부풀지 않게)
    first &= created
    engaged = {
        u for u in first if max(counts.get(u, 0), event_counts.get(u, 0)) >= ENGAGED_MESSAGES
    }
    requested = await _ids(
        db,
        select(SubscriptionRequest.user_id)
        .distinct()
        .where(SubscriptionRequest.user_id.in_(cohort)),
    ) | await _event_users(db, "subscription_requested", cohort)
    sets = {
        "signed_up": signed,
        "consented": consented,
        "persona_created": created,
        "recordings_uploaded": uploaded,
        "build_done": built,
        "first_message": first,
        "engaged": engaged,
        "subscription_requested": requested,
    }
    # 뒤 단계에 온 사람은 앞 단계도 거친 것으로 센다(기록이 빠진 단계가 있어도 깔때기가
    # 늘 줄어들게 — 앞 단계 대비 100%를 넘지 않는다)
    counts: dict[str, int] = {}
    later: set = set()
    for key, _ in reversed(FUNNEL):
        later = later | (sets[key] & signed)
        counts[key] = len(later)
    return [{"key": key, "label": label, "users": counts[key]} for key, label in FUNNEL]


async def _distributions(db) -> dict[str, dict[str, int]]:
    rows = (
        await db.execute(
            select(
                Persona.setup_step,
                Persona.basics,
                Persona.voice_id,
                Persona.voice_provider,
                Person.life_status,
            )
            .join(Person, Person.id == Persona.person_id)
            .where(_customer_personas())
        )
    ).all()
    steps = dict.fromkeys(STEPS, 0)
    life = dict.fromkeys(LIFE_KEYS, 0)
    voices = dict.fromkeys(VOICE_KEYS, 0)
    for step, basics, voice_id, provider, life_status in rows:
        steps[step] = steps.get(step, 0) + 1
        status = (basics or {}).get("status")
        if status not in LIFE_KEYS:
            status = {"DECEASED": "deceased", "LIVING": "alive"}.get(life_status)
        if status:
            life[status] += 1
        key = (provider or "none") if voice_id else "none"
        voices[key] = voices.get(key, 0) + 1
    plans = dict.fromkeys(REQUEST_PLANS, 0)
    for plan, n in (
        await db.execute(
            select(SubscriptionRequest.plan, func.count(SubscriptionRequest.id))
            .where(
                SubscriptionRequest.status == billing.REQUESTED,
                SubscriptionRequest.user_id.not_in(_admins()),
            )
            .group_by(SubscriptionRequest.plan)
        )
    ).all():
        plans[plan] = int(n)
    return {
        "personasByStep": steps,
        "lifeStatus": life,
        "voicesByProvider": voices,
        "requestsByPlan": plans,
    }


async def _builds(db, start: datetime) -> dict[str, Any]:
    names = ("build_started", "build_done", "build_failed")
    rows = (
        await db.execute(
            select(AnalyticsEvent.name, AnalyticsEvent.props).where(
                AnalyticsEvent.name.in_(names),
                AnalyticsEvent.created_at >= start,
                or_(AnalyticsEvent.user_id.is_(None), AnalyticsEvent.user_id.not_in(_admins())),
            )
        )
    ).all()
    counts = Counter(name for name, _ in rows)
    seconds = [
        (props or {}).get("seconds")
        for name, props in rows
        if name == "build_done" and isinstance((props or {}).get("seconds"), int | float)
    ]
    return {
        "started": counts["build_started"],
        "done": counts["build_done"],
        "failed": counts["build_failed"],
        "avgSeconds": round(sum(seconds) / len(seconds)) if seconds else None,
    }


async def _daily(db, start: datetime, days: int) -> list[dict[str, Any]]:
    first_day = start.astimezone(KST).date()
    dates = [(first_day + timedelta(days=i)).isoformat() for i in range(days)]
    signups = Counter(
        _kst_date(at)
        for at in (
            await db.scalars(
                select(User.created_at).where(_customer_users(), User.created_at >= start)
            )
        ).all()
    )
    messages: Counter[str] = Counter()
    active: dict[str, set[str]] = defaultdict(set)
    for at, uid in (
        await db.execute(
            select(Message.created_at, Conversation.user_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.role == "user",
                Message.created_at >= start,
                Conversation.user_id.not_in(_admins()),
            )
        )
    ).all():
        day = _kst_date(at)
        messages[day] += 1
        active[day].add(uid)
    builds: Counter[str] = Counter()
    requests: Counter[str] = Counter()
    for at, uid, name in (
        await db.execute(
            select(AnalyticsEvent.created_at, AnalyticsEvent.user_id, AnalyticsEvent.name).where(
                AnalyticsEvent.created_at >= start,
                or_(AnalyticsEvent.user_id.is_(None), AnalyticsEvent.user_id.not_in(_admins())),
            )
        )
    ).all():
        day = _kst_date(at)
        if uid:
            active[day].add(uid)
        if name == "build_done":
            builds[day] += 1
        elif name == "subscription_requested":
            requests[day] += 1
    return [
        {
            "date": day,
            "signups": signups[day],
            "activeUsers": len(active[day]),
            "messages": messages[day],
            "builds": builds[day],
            "requests": requests[day],
        }
        for day in dates
    ]


async def _top_screens(db, start: datetime, limit: int = 20) -> list[dict[str, Any]]:
    views: Counter[str] = Counter()
    users: dict[str, set[str]] = defaultdict(set)
    for uid, props in (
        await db.execute(
            select(AnalyticsEvent.user_id, AnalyticsEvent.props).where(
                AnalyticsEvent.name == "screen_view",
                AnalyticsEvent.created_at >= start,
                or_(AnalyticsEvent.user_id.is_(None), AnalyticsEvent.user_id.not_in(_admins())),
            )
        )
    ).all():
        screen = str((props or {}).get("screen") or "unknown")[:80]
        views[screen] += 1
        if uid:
            users[screen].add(uid)
    return [
        {"screen": screen, "views": n, "users": len(users[screen])}
        for screen, n in views.most_common(limit)
    ]


async def _request_rows(db, since: datetime | None = None, limit: int | None = None):
    query = (
        select(SubscriptionRequest, Persona, Person, User)
        .join(Persona, Persona.id == SubscriptionRequest.persona_id)
        .join(Person, Person.id == Persona.person_id)
        .join(User, User.id == SubscriptionRequest.user_id)
        .where(_live_customer())
        .order_by(SubscriptionRequest.created_at.desc())
    )
    if since is not None:
        query = query.where(SubscriptionRequest.updated_at >= since)
    if limit is not None:
        query = query.limit(limit)
    return [
        {
            "createdAt": _aware(req.created_at),
            "updatedAt": _aware(req.updated_at),
            "email": _contact_email(user.email),
            # 이메일을 주지 않은 소셜 가입자도 누군지 알아보게(카카오 닉네임 등)
            "name": user.name,
            "personaTitle": persona_title(persona, person),
            "relation": persona_relation(persona, person),
            "plan": req.plan,
            "planName": billing.plan_name(req.plan),
            "status": req.status,
        }
        for req, persona, person, user in (await db.execute(query)).all()
    ]


async def _recent_signups(db, limit: int = 30) -> list[dict[str, Any]]:
    users = (
        await db.scalars(
            select(User).where(_live_customer()).order_by(User.created_at.desc()).limit(limit)
        )
    ).all()
    first: dict[str, str] = {}
    if users:
        for uid, provider in (
            await db.execute(
                select(SocialAccount.user_id, SocialAccount.provider)
                .where(SocialAccount.user_id.in_([u.id for u in users]))
                .order_by(SocialAccount.created_at)
            )
        ).all():
            first.setdefault(uid, provider)
    return [
        {
            "createdAt": _aware(u.created_at),
            "email": None if u.email.endswith(SOCIAL_PLACEHOLDER_EMAIL_SUFFIX) else u.email,
            "name": u.name,
            "method": first.get(u.id, "email"),
        }
        for u in users
    ]


async def _signup_rows(db) -> list[dict[str, Any]]:
    """가입자 전체(처음부터, 오래된 순). 소셜 가입은 받은 이메일이 없으면 빈칸."""
    users = (await db.scalars(select(User).where(_live_customer()).order_by(User.created_at))).all()
    first: dict[str, str] = {}
    social_email: dict[str, str] = {}
    for uid, provider, email in (
        await db.execute(
            select(SocialAccount.user_id, SocialAccount.provider, SocialAccount.email).order_by(
                SocialAccount.created_at
            )
        )
    ).all():
        first.setdefault(uid, provider)
        if email:
            social_email.setdefault(uid, email)
    personas = dict(
        (
            await db.execute(
                select(Person.owner_user_id, func.count(Person.id)).group_by(Person.owner_user_id)
            )
        ).all()
    )
    return [
        {
            "createdAt": _aware(u.created_at),
            "email": social_email.get(u.id)
            if u.email.endswith(SOCIAL_PLACEHOLDER_EMAIL_SUFFIX)
            else u.email,
            "name": u.name,
            "method": first.get(u.id, "email"),
            "status": u.status,
            "personas": personas.get(u.id, 0),
        }
        for u in users
    ]


# ────────────────────────── 사람별 진행 단계 ──────────────────────────


def _persona_stage(step: str, ready_at: datetime | None, has_material: bool) -> str:
    # 한 번 다 만든 대화 상대는 다시 만드는 중이어도 완성으로 본다
    if ready_at is not None:
        return "ready"
    return STEP_STAGE.get(step) or ("materials" if has_material else "persona_started")


async def _user_stages(db) -> dict[str, list[dict[str, Any]]]:
    """가입자(관리자·탈퇴 제외)마다 지금 단계와 몇 가지 수(최근 가입 순), 단계별 사람 수.

    사람마다 묻지 않고 묶음 질의 몇 번으로 센다. 대화 중은 제 대화 상대와 나눈 대화만 본다
    (가족이 나눠 준 대화 상대와만 이야기한 사람은 family_member).
    """
    cohort = select(User.id).where(_live_customer())
    users = (
        await db.execute(
            select(User.id, User.email, User.name, User.created_at)
            .where(_live_customer())
            .order_by(User.created_at.desc())
        )
    ).all()
    method: dict[str, str] = {}
    social_email: dict[str, str] = {}
    for uid, provider, email in (
        await db.execute(
            select(SocialAccount.user_id, SocialAccount.provider, SocialAccount.email)
            .where(SocialAccount.user_id.in_(cohort))
            .order_by(SocialAccount.created_at)
        )
    ).all():
        method.setdefault(uid, provider)
        if email:
            social_email.setdefault(uid, email)
    consented = await _ids(
        db,
        select(Consent.user_id)
        .where(
            Consent.user_id.in_(cohort),
            Consent.consent_type.in_(legal.ACCOUNT_TYPES),
            Consent.agreed.is_(True),
        )
        .group_by(Consent.user_id)
        .having(func.count(distinct(Consent.consent_type)) >= len(legal.ACCOUNT_TYPES)),
    ) | await _event_users(db, "consent_accepted", cohort)

    # 만든 대화 상대: 녹음·글 자료가 있나, 어느 단계인가
    owned_ids = (
        select(Persona.id)
        .join(Person, Person.id == Persona.person_id)
        .where(Person.owner_user_id.in_(cohort))
    )
    with_material = await _ids(
        db, select(Recording.persona_id).distinct().where(Recording.persona_id.in_(owned_ids))
    ) | await _ids(
        db,
        select(PersonaMaterial.persona_id)
        .distinct()
        .where(PersonaMaterial.persona_id.in_(owned_ids)),
    )
    owner_of: dict[str, str] = {}
    rank: dict[str, int] = {}
    personas: Counter[str] = Counter()
    ready: Counter[str] = Counter()
    for pid, uid, step, ready_at in (
        await db.execute(
            select(Persona.id, Person.owner_user_id, Persona.setup_step, Persona.ready_at)
            .join(Person, Person.id == Persona.person_id)
            .where(Person.owner_user_id.in_(cohort))
        )
    ).all():
        stage = _persona_stage(step, ready_at, pid in with_material)
        owner_of[pid] = uid
        personas[uid] += 1
        ready[uid] += stage == "ready"
        rank[uid] = max(rank.get(uid, 0), STAGE_RANK[stage])

    # 대화: 사람·대화 상대별 내 메시지 수와 마지막 때
    messages: Counter[str] = Counter()
    last_chat: dict[str, datetime | None] = {}
    chatted_own: set[str] = set()
    for uid, pid, n, last in (
        await db.execute(
            select(
                Conversation.user_id,
                Conversation.persona_id,
                func.count(Message.id),
                func.max(Message.created_at),
            )
            .join(Message, Message.conversation_id == Conversation.id)
            .where(Message.role == "user", Conversation.user_id.in_(cohort))
            .group_by(Conversation.user_id, Conversation.persona_id)
        )
    ).all():
        messages[uid] += int(n)
        last_chat[uid] = _latest(last_chat.get(uid), last)
        if owner_of.get(pid) == uid:
            chatted_own.add(uid)
    conversations = dict(
        (
            await db.execute(
                select(Conversation.user_id, func.count(Conversation.id))
                .where(Conversation.user_id.in_(cohort))
                .group_by(Conversation.user_id)
            )
        ).all()
    )
    last_event = dict(
        (
            await db.execute(
                select(AnalyticsEvent.user_id, func.max(AnalyticsEvent.created_at))
                .where(AnalyticsEvent.user_id.in_(cohort))
                .group_by(AnalyticsEvent.user_id)
            )
        ).all()
    )

    request: dict[str, tuple[str, str]] = {}
    for uid, plan, req_status in (
        await db.execute(
            select(
                SubscriptionRequest.user_id, SubscriptionRequest.plan, SubscriptionRequest.status
            )
            .where(SubscriptionRequest.user_id.in_(cohort))
            .order_by(SubscriptionRequest.updated_at.desc())
        )
    ).all():
        # 같은 순위면 최근 것(먼저 읽은 것)을 둔다
        best = request.get(uid)
        if best is None or REQUEST_ORDER.get(req_status, 0) > REQUEST_ORDER.get(best[0], 0):
            request[uid] = (req_status, plan)
    paid: dict[str, str] = {}
    for uid, plan in (
        await db.execute(
            select(Subscription.user_id, Subscription.plan).where(
                Subscription.user_id.in_(cohort),
                Subscription.status.in_([billing.ACTIVE, billing.PAST_DUE]),
            )
        )
    ).all():
        if paid.get(uid) != billing.TOP_PLAN:
            paid[uid] = plan

    # 가족 공유: 남의 대화 상대에 들어온 자리 수, 내 대화 상대에 들어온 가족 수
    # (내 대화 상대 둘에 들어와도 한 사람)
    member_of = dict(
        (
            await db.execute(
                select(PersonaShare.user_id, func.count(PersonaShare.id))
                .where(PersonaShare.user_id.in_(cohort))
                .group_by(PersonaShare.user_id)
            )
        ).all()
    )
    joined = dict(
        (
            await db.execute(
                select(Person.owner_user_id, func.count(distinct(PersonaShare.user_id)))
                .select_from(PersonaShare)
                .join(Persona, Persona.id == PersonaShare.persona_id)
                .join(Person, Person.id == Persona.person_id)
                .where(PersonaShare.user_id.is_not(None), Person.owner_user_id.in_(cohort))
                .group_by(Person.owner_user_id)
            )
        ).all()
    )

    rows: list[dict[str, Any]] = []
    reached = [0] * len(USER_STAGES)
    current: Counter[str] = Counter()
    for uid, email, name, created_at in users:
        r = max(rank.get(uid, 0), STAGE_RANK["consented"] if uid in consented else 0)
        if uid in chatted_own:
            r = max(r, STAGE_RANK["chatting"])
        req_status, req_plan = request.get(uid, (None, None))
        if req_status in (billing.REQUESTED, billing.CONVERTED):
            r = max(r, STAGE_RANK["requested"])
        if uid in paid:
            r = max(r, STAGE_RANK["subscribed"])
        for i in range(r + 1):
            reached[i] += 1
        stage, label = USER_STAGES[r]
        if uid in member_of and r <= STAGE_RANK["consented"]:
            stage, label = FAMILY_STAGE
        current[stage] += 1
        # owner 만든 분 · member 초대받은 가족 · both 둘 다 · None 아직 없음
        role = {(True, False): "owner", (False, True): "member", (True, True): "both"}.get(
            (personas[uid] > 0, uid in member_of)
        )
        rows.append(
            {
                "createdAt": _aware(created_at),
                "email": social_email.get(uid)
                if email.endswith(SOCIAL_PLACEHOLDER_EMAIL_SUFFIX)
                else email,
                # 이메일을 주지 않은 소셜 가입자도 알아보게
                "name": name,
                "method": method.get(uid, "email"),
                "stage": stage,
                "stageLabel": label,
                "personaCount": personas[uid],
                "readyCount": ready[uid],
                "conversationCount": int(conversations.get(uid, 0)),
                "messageCount": messages[uid],
                "lastChatAt": last_chat.get(uid),
                "lastActiveAt": _latest(created_at, last_chat.get(uid), last_event.get(uid)),
                "requestStatus": req_status,
                "requestPlan": req_plan,
                "subscriptionPlan": paid.get(uid),
                "role": role,
                "memberOf": int(member_of.get(uid, 0)),
                "familyJoined": int(joined.get(uid, 0)),
            }
        )
    summary = [
        {
            "key": key,
            "label": label,
            "reached": reached[i],
            "current": current[key],
            "branch": False,
        }
        for i, (key, label) in enumerate(USER_STAGES)
    ]
    summary.append(
        {
            "key": FAMILY_STAGE[0],
            "label": FAMILY_STAGE[1],
            "reached": len(member_of),
            "current": current[FAMILY_STAGE[0]],
            "branch": True,
        }
    )
    return {"userStages": summary, "users": rows}


def _period_start(days: int, current: datetime) -> datetime:
    """오늘(한국 날짜)을 넣어 days 일 전 0시(한국 시간)."""
    today = current.astimezone(KST).date()
    first = today - timedelta(days=days - 1)
    return datetime(first.year, first.month, first.day, tzinfo=KST).astimezone(UTC)


async def build_stats(db, days: int) -> dict[str, Any]:
    current = billing.now()
    start = _period_start(days, current)
    return {
        "generatedAt": current,
        "paymentsOpen": billing.payments_open(),
        # AI 회사(OpenAI) 문제가 아직 풀리지 않았으면(크레딧·키·요청 한도) 관리자 화면에 경고
        "aiProviderTrouble": ai.provider_trouble(),
        # 목소리 회사(Fish Audio) 문제도 따로(VOICE_PROVIDER_*). 글 대화는 그대로 된다
        "voiceProviderTrouble": ai.voice_provider_trouble(),
        "days": days,
        "totals": await _totals(db, current),
        "active": {
            "d1": len(await _active_users(db, current - timedelta(days=1))),
            "d7": len(await _active_users(db, current - timedelta(days=7))),
            "d30": len(await _active_users(db, current - timedelta(days=30))),
        },
        "funnel": await _funnel(db, start),
        **await _distributions(db),
        "builds": await _builds(db, start),
        "daily": await _daily(db, start, days),
        "topScreens": await _top_screens(db, start),
        "recentRequests": [
            {k: v for k, v in row.items() if k != "updatedAt"}
            for row in await _request_rows(db, limit=50)
        ],
        "recentSignups": await _recent_signups(db),
        # 사람별 진행 단계(가입자 전부, 최근 가입 순)와 단계별 사람 수(도달·지금)
        **await _user_stages(db),
    }


@router.get("/stats", response_model=Envelope[dict])
async def stats(_: AdminUser, db: DB, days: int = Query(30, ge=1, le=365)):
    """개발자 화면 · 사용 통계 한 묶음(관리자만)."""
    return Envelope(data=await build_stats(db, days))


def _iso(value: datetime | None) -> str:
    value = _aware(value)
    return value.isoformat() if value else ""


# 엑셀이 수식으로 읽는 첫 글자. 고객이 적은 값(이름·이메일)이 수식으로 돌지 않게 앞에 ' 를 붙인다.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(FORMULA_START):
        return "'" + value
    return value


def _csv(header: list[str], rows: list[list[Any]], filename: str) -> Response:
    """엑셀에서 한글이 깨지지 않게 UTF-8 BOM 을 붙인다."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows([_cell(value) for value in row] for row in rows)
    return Response(
        content=("﻿" + buffer.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/stats/export")
async def export_stats(
    _: AdminUser,
    db: DB,
    kind: Literal["signups", "requests", "events"] = Query("requests"),
    days: int = Query(30, ge=1, le=3650),
):
    """CSV 내려받기.

    signups = 가입자 전체(처음부터), requests = 구독 신청 전체(연락용, 처음부터),
    events = 이벤트 원본(고른 기간 days 만 — 양이 많다).
    """
    current = billing.now()
    since = current - timedelta(days=days)
    stamp = current.astimezone(KST).strftime("%Y%m%d")
    if kind == "signups":
        rows = await _signup_rows(db)
        return _csv(
            ["created_at", "email", "name", "method", "status", "personas"],
            [
                [
                    _iso(r["createdAt"]),
                    r["email"] or "",
                    r["name"] or "",
                    r["method"],
                    r["status"],
                    r["personas"],
                ]
                for r in rows
            ],
            f"talkto_signups_{stamp}.csv",
        )
    if kind == "requests":
        rows = await _request_rows(db)
        return _csv(
            [
                "created_at",
                "updated_at",
                "email",
                "persona_title",
                "relation",
                "plan",
                "plan_name",
                "status",
            ],
            [
                [
                    _iso(r["createdAt"]),
                    _iso(r["updatedAt"]),
                    r["email"] or "",
                    r["personaTitle"],
                    r["relation"] or "",
                    r["plan"],
                    r["planName"],
                    r["status"],
                ]
                for r in rows
            ],
            f"talkto_requests_{stamp}.csv",
        )
    events = (
        await db.scalars(
            _events(AnalyticsEvent.created_at >= since).order_by(AnalyticsEvent.created_at)
        )
    ).all()
    return _csv(
        ["id", "created_at", "user_id", "persona_id", "name", "props"],
        [
            [
                e.id,
                _iso(e.created_at),
                e.user_id or "",
                e.persona_id or "",
                e.name,
                json.dumps(e.props or {}, ensure_ascii=False, sort_keys=True),
            ]
            for e in events
        ],
        f"talkto_events_{stamp}.csv",
    )
