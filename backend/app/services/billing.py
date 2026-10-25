"""요금제·무료 체험·구독 결제. 결제·체험·대화 횟수는 모두 페르소나마다 따로다.

    페르소나마다 무료 체험 7일(그분 목소리, 대화 30번) — 다 만든 뒤부터 센다.
    계정 하나에 체험은 대화 상대 1개(지운 것도 센다).
    → 페르소나마다 추억(memories, 9,900원 · 한 달 대화 50번) / 가족(family, 29,900원 · 300번)
      (값은 설정 plan_* — 아래 plans())

정식 요금과 '초기 가족 요금'(첫 100가족 · 12개월)을 함께 둔다. 초기 요금은 가족(계정) 단위라
User.beta_until 까지 그 가족의 모든 페르소나 결제에 쓴다.

똑 닮은 목소리(premium, PVC) 요금제는 2026-10-17 에 없앴다. 남은 구독·신청·결제 창은 서버가 켜질
때 고친다(db.session._retire_premium). 그때 낸 결제 기록은 그대로 둔다(RETIRED_PLAN_NAMES).

결제는 카드 자동결제(빌링)다. 카드를 한 번 등록하면 결제 회사가 빌링키를 주고, 매달 그 키로
결제한다. 카드 번호는 우리 서버에 오지 않는다(결제 회사 창에서 입력). 한 페르소나에 등록한 카드로
다른 페르소나 구독도 시작할 수 있다(start_with_card).

    결제 회사 = 토스페이먼츠(TOSS_CLIENT_KEY·TOSS_SECRET_KEY 가 있을 때)
              = 시험 결제(mock, 로컬에서 키가 없을 때 — 돈이 오가지 않는다)

⛔ 실제 돈을 받는 결제(라이브 키)는 사업자등록·PG 가맹·자동결제 계약·변호사 회신 뒤에 켠다.

결제 전 모드(PAYMENTS_OPEN=false, 기본): 바로 결제하지 않고 구독 신청(SubscriptionRequest)만
받는다. 다 만든 대화 상대는 체험 자리를 쓰지 않고 한도 없이 쓴다(PRELAUNCH). 결제가 열리면
원래 규칙으로 체험이 시작되고, 신청한 대화 상대가 결제에 성공하면 신청이 CONVERTED 가 된다.

청약철회·환불 판단(2026-10-22 법률 자문 · WO 1-7 · SPEC §3): 결제마다 '결제 후 첫 대화'(초대한
가족의 대화도)와, 첫 결제 때 그 대화 상대가 무료 체험을 실제로 썼는지를 남긴다. 관리자 환불 화면은
이것으로 환불 대상을 짐작해 보인다(admin_rows). 환불은 대표가 토스 상점관리자에서 손으로 하고 기록만
남긴다. 결제·갱신으로 기간이 정해지면 계약 내용을 앱 알림(메일도)으로 남긴다(contract_mail).
"""

from __future__ import annotations

import base64
import calendar
import json
import logging
import math
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from fractions import Fraction
from html import escape
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    JobStatus,
    Payment,
    Persona,
    PersonaJob,
    SetupStep,
    Subscription,
    SubscriptionRequest,
    UsageEvent,
    User,
    UserRole,
)

logger = logging.getLogger(__name__)
# 한국은 서머타임이 없어 +9시간 고정이면 된다(윈도·슬림 이미지에 시간대 자료가 없어도 되게)
KST = timezone(timedelta(hours=9))

ACTIVE, PAST_DUE, CANCELED = "ACTIVE", "PAST_DUE", "CANCELED"
GRACE_DAYS = 3  # 갱신 결제가 실패해도 이만큼은 계속 쓰게 두고 매일 다시 결제해 본다
RENEWAL_GRACE = timedelta(days=1)  # 결제일이 지나 갱신을 기다리는 동안
RETRY_HOURS = 20


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price: int  # 정식 요금(원, 부가세 포함)
    beta_price: int | None  # 초기 가족 요금. 없으면 정식 요금만
    voice: str  # 늘 ivc(그분 목소리 하나). 화면이 읽어서 칸은 둔다
    conversations: int | None  # 한 달 대화 횟수(None = 제한 없음)
    summary: str
    rank: int  # 올리기·내리기 순서


def plans() -> dict[str, Plan]:
    """요금제. 값은 .env 로 바꿀 수 있다(기본값은 2026-10-21 요금 변경 기준, config.py)."""
    s = get_settings()
    return {
        "trial": Plan(
            "trial",
            "무료 체험",
            0,
            None,
            "ivc",
            s.trial_conversations,
            f"{s.trial_days}일 동안 무료 · 그분 목소리로 대답 · 대화 {s.trial_conversations:,}번",
            0,
        ),
        "memories": Plan(
            "memories",
            "추억",
            s.plan_memories_price,
            s.plan_memories_beta_price,
            "ivc",
            s.plan_memories_conversations,
            f"그분 목소리로 대답 · 한 달 대화 {s.plan_memories_conversations:,}번",
            1,
        ),
        "family": Plan(
            "family",
            "가족",
            s.plan_family_price,
            s.plan_family_beta_price,
            "ivc",
            s.plan_family_conversations,
            f"그분 목소리로 대답 · 한 달 대화 {s.plan_family_conversations:,}번",
            2,
        ),
    }


PAID_PLANS = ("memories", "family")
# 가장 높은 요금제(관리자 권리, 없앤 요금제의 남은 결제를 정할 때)
TOP_PLAN = "family"
# 없앤 요금제의 이름. 그때 낸 결제 기록·신청을 보여 줄 때만 쓴다.
RETIRED_PLAN_NAMES = {"premium": "똑 닮은 목소리(종료)"}


def plan_name(plan_id: str | None) -> str | None:
    """요금제 이름. 없앤 요금제는 RETIRED_PLAN_NAMES, 모르는 것은 받은 그대로."""
    plan = plans().get(plan_id or "")
    return plan.name if plan else RETIRED_PLAN_NAMES.get(plan_id or "", plan_id)


class BillingError(RuntimeError):
    """화면에 그대로 보여줄 수 있는 결제 오류."""


# 결제 회사가 설정되지 않아 지금은 결제를 받을 수 없을 때
NOT_OPEN = "지금은 결제할 수 없어요. 잠시 뒤 다시 해 주세요."
# 결과를 아직 모르는 결제가 있을 때(정해질 때까지 새로 결제하지 않는다)
CHECKING = "방금 하신 결제가 잘 됐는지 확인하고 있어요."


# ────────────────────────── 지금 쓸 수 있는 것 ──────────────────────────


@dataclass
class Entitlement:
    """페르소나 하나가 지금 쓸 수 있는 것. 결제·체험·대화 횟수는 모두 페르소나마다 따로다."""

    persona_id: str
    plan: str  # trial / memories / family / none
    # TRIAL · NOT_STARTED(다 만들기 전, 체험은 다 만든 뒤 시작) · ACTIVE · PAST_DUE · CANCELING
    # · EXPIRED(체험·구독이 끝남) · NO_TRIAL(계정의 체험 페르소나를 다 씀) · ADMIN
    # · WAITING_OWNER(체험을 아직 열지 않았고 주인이 볼 때 연다 — start_trial=False 일 때만)
    status: str
    active: bool
    voice: str  # 늘 ivc(화면 호환으로 둔다)
    trial_ends_at: datetime | None
    period_end: datetime | None
    next_plan: str | None
    conversations_limit: int | None
    conversations_used: int
    # 횟수가 다시 채워지는 때(구독 = 다음 결제일). 체험·해지 예정은 채워지지 않는다(None).
    resets_at: datetime | None = None
    # 이때부터의 대화를 센다(구독 = 이번 기간 시작, 체험 = None 처음부터)
    counted_since: datetime | None = None
    # 결제 전 모드: 다 만든 날. 이날부터 30일마다 대화 횟수를 다시 센다.
    made_at: datetime | None = None

    @property
    def conversations_left(self) -> int | None:
        if self.conversations_limit is None:
            return None
        return max(0, self.conversations_limit - self.conversations_used)


def now() -> datetime:
    return datetime.now(UTC)


def app_url(route: str) -> str:
    """웹 화면 주소(웹은 #/… 경로를 쓴다). 예: app_url('/legal/terms')."""
    return f"{get_settings().frontend_url.rstrip('/')}/#{route}"


def _aware(value: datetime | None) -> datetime | None:
    """SQLite 는 시간대를 지워 돌려준다. 저장할 때 UTC 로 넣으므로 UTC 로 읽는다."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


async def conversations_since(db: AsyncSession, persona_id: str, since: datetime | None) -> int:
    query = select(func.count(UsageEvent.id)).where(
        UsageEvent.persona_id == persona_id, UsageEvent.kind == "conversation"
    )
    if since is not None:
        query = query.where(UsageEvent.created_at >= since)
    return int(await db.scalar(query) or 0)


async def reserve_conversation(
    db: AsyncSession,
    user: User,
    persona: Persona,
    ent: Entitlement,
    *,
    kind: str = "conversation",
    limit: int | None = -1,
) -> str | None:
    """보내기 전에 대화 1번을 잡아 둔다(잡은 줄의 id).

    동시에 여러 번 보내도 횟수를 넘지 않게, 잡은 순서가 횟수 안에 드는지 본다. 넘으면 None(잡지
    않음). 대답을 받지 못하면 release_conversation 으로 돌려준다(실패한 대화는 세지 않는다).
    """
    # kind: conversation(대화 횟수) · text_only(횟수를 다 쓴 뒤의 글 대답, 공정 사용 상한만 본다)
    limit = ent.conversations_limit if limit == -1 else limit
    # 같은 페르소나의 잡기를 한 줄로 세운다(Postgres: 앞선 잡기가 끝날 때까지 기다린다 — 그래야
    # 잡은 시각 순서와 커밋 순서가 같아 마지막 한 자리를 둘이 함께 잡지 않는다. SQLite 는 쓰기가
    # 원래 한 줄이라 FOR UPDATE 를 무시해도 된다).
    await db.execute(select(Persona.id).where(Persona.id == persona.id).with_for_update())
    event = UsageEvent(user_id=user.id, persona_id=persona.id, kind=kind)
    db.add(event)
    await db.commit()
    await db.refresh(event)
    # id 를 미리 잡아 둔다. 실패해 되돌리면(rollback) event 를 다시 읽지 못한다.
    event_id = event.id
    if limit is None:
        return event_id
    earlier = or_(
        UsageEvent.created_at < event.created_at,
        (UsageEvent.created_at == event.created_at) & (UsageEvent.id <= event.id),
    )
    query = select(func.count(UsageEvent.id)).where(
        UsageEvent.persona_id == persona.id, UsageEvent.kind == kind, earlier
    )
    if ent.counted_since is not None:
        query = query.where(UsageEvent.created_at >= ent.counted_since)
    if int(await db.scalar(query) or 0) > limit:
        await release_conversation(db, event_id)
        return None
    return event_id


async def release_conversation(db: AsyncSession, event_id: str | None) -> None:
    """대답을 받지 못한 대화는 세지 않는다."""
    if event_id is None:
        return
    await db.execute(delete(UsageEvent).where(UsageEvent.id == event_id))
    await db.commit()


async def get_subscription(db: AsyncSession, persona_id: str) -> Subscription | None:
    return await db.scalar(select(Subscription).where(Subscription.persona_id == persona_id))


async def user_subscriptions(db: AsyncSession, user_id: str) -> list[Subscription]:
    return list(
        (await db.scalars(select(Subscription).where(Subscription.user_id == user_id))).all()
    )


def trials_left(user: User) -> int:
    return max(0, get_settings().trial_personas_per_account - (user.trials_used or 0))


async def _start_trial(db: AsyncSession, user: User, persona: Persona) -> bool:
    """다 만든 페르소나의 체험을 시작한다. 계정의 체험 페르소나를 다 썼으면 False.

    지운 페르소나도 체험을 쓴 것으로 센다(지우고 다시 만들어 체험을 되살리지 못하게).
    동시에 여러 곳에서 불러도 상한을 넘지 않게, 먼저 사용자 줄에서 자리를 조건부로 잡고
    그다음 페르소나 체험을 연다(이미 열렸으면 잡은 자리를 돌려준다).
    """
    cap = get_settings().trial_personas_per_account
    end = now() + timedelta(days=get_settings().trial_days)
    took = await db.execute(
        update(User)
        .where(User.id == user.id, func.coalesce(User.trials_used, 0) < cap)
        .values(trials_used=func.coalesce(User.trials_used, 0) + 1)
    )
    if took.rowcount == 1:
        opened = await db.execute(
            update(Persona)
            .where(Persona.id == persona.id, Persona.trial_ends_at.is_(None))
            .values(trial_ends_at=end)
        )
        if opened.rowcount != 1:  # 다른 곳이 이미 열었다
            await db.execute(
                update(User)
                .where(User.id == user.id)
                .values(trials_used=func.coalesce(User.trials_used, 0) - 1)
            )
    await db.commit()
    await db.refresh(persona)
    await db.refresh(user)
    return persona.trial_ends_at is not None


async def entitlement(
    db: AsyncSession, user: User, persona: Persona, *, start_trial: bool = True
) -> Entitlement:
    """이 페르소나가 지금 쓸 수 있는 것. user 는 늘 주인이다(관리자·결제 시험 계정·체험 자리).

    start_trial=False 면 다 만들었는데 체험을 아직 열지 않은 페르소나의 체험을 열지 않고
    WAITING_OWNER(쓸 수 없음)로 둔다. 함께 쓰는 가족의 대화가 주인의 체험 자리를 쓰지 않게 한다.
    """
    all_plans = plans()
    current = now()
    if user.role == UserRole.ADMIN:
        made = resets = None
        since = current - timedelta(days=30)
        if not payments_open(user) and persona.setup_step == SetupStep.READY:
            made = await made_at(db, persona)
            since, resets = prelaunch_cycle(made, current)
        used = await conversations_since(db, persona.id, since)
        return Entitlement(
            persona.id,
            TOP_PLAN,
            "ADMIN",
            True,
            "ivc",
            None,
            None,
            None,
            None,
            used,
            resets_at=resets,
            counted_since=since,
            made_at=made,
        )
    sub = await get_subscription(db, persona.id)
    if sub and sub.status in (ACTIVE, PAST_DUE) and sub.current_period_end:
        end = _aware(sub.current_period_end)
        if sub.status == PAST_DUE:
            grace = end + timedelta(days=GRACE_DAYS)
        elif sub.cancel_at_period_end:
            grace = end
        else:
            # 결제일이 지나도 갱신 작업(10분마다)·결과 확인 중인 결제를 기다리는 동안은 끊지 않는다
            grace = end + RENEWAL_GRACE
        if grace > current:
            plan = all_plans[sub.plan]
            status = sub.status
            if sub.status == ACTIVE and sub.cancel_at_period_end:
                status = "CANCELING"
            used = await conversations_since(db, persona.id, _aware(sub.current_period_start))
            return Entitlement(
                persona.id,
                sub.plan,
                status,
                True,
                plan.voice,
                _aware(persona.trial_ends_at),
                end,
                sub.next_plan,
                plan.conversations,
                used,
                # 해지 예정·결제 실패(다시 결제되면 채워짐)는 다시 채워지는 날이 없다
                None if sub.cancel_at_period_end or sub.status == PAST_DUE else end,
                _aware(sub.current_period_start),
            )
    trial = all_plans["trial"]
    if not payments_open(user):
        return await _prelaunch(db, persona)
    if persona.trial_ends_at is None and sub is not None and sub.current_period_start:
        # 체험 없이 바로 결제했던 대화 상대의 구독이 끝났다: '체험 없음'이 아니라 '끝남'이다
        # (끝난 뒤에 체험을 새로 열지도 않는다)
        return Entitlement(persona.id, "none", "EXPIRED", False, "ivc", None, None, None, 0, 0)
    if persona.trial_ends_at is None:
        if persona.setup_step != SetupStep.READY:
            # 체험은 다 만든 뒤(들어 볼 수 있을 때)부터 센다
            return Entitlement(
                persona.id,
                "trial",
                "NOT_STARTED",
                True,
                "ivc",
                None,
                None,
                None,
                trial.conversations,
                0,
            )
        if not start_trial:
            return Entitlement(
                persona.id, "none", "WAITING_OWNER", False, "ivc", None, None, None, 0, 0
            )
        if not await _start_trial(db, user, persona):
            return Entitlement(persona.id, "none", "NO_TRIAL", False, "ivc", None, None, None, 0, 0)
    trial_end = _aware(persona.trial_ends_at)
    if trial_end > current:
        # 체험은 페르소나마다 한 번뿐이라 그 페르소나의 대화를 처음부터 센다
        used = await conversations_since(db, persona.id, None)
        return Entitlement(
            persona.id,
            "trial",
            "TRIAL",
            True,
            "ivc",
            trial_end,
            None,
            None,
            trial.conversations,
            used,
        )
    return Entitlement(persona.id, "none", "EXPIRED", False, "ivc", trial_end, None, None, 0, 0)


def payment_testers() -> set[str]:
    """결제 시험 계정(PAYMENTS_TEST_EMAILS)의 이메일. 대소문자는 가리지 않는다."""
    raw = get_settings().payments_test_emails or ""
    return {email.strip().lower() for email in raw.split(",") if email.strip()}


def payments_open(user: User | None = None) -> bool:
    """결제가 열렸나. 닫혀 있어도 결제 시험 계정에는 연다 — 결제 회사가 시험 결제일 때만.

    결제 시험 계정은 실제 돈이 나가지 않는 결제로만 결제창을 확인하게 한다(실제 키면 닫힌 그대로).
    """
    if get_settings().payments_open:
        return True
    if user is None or (user.email or "").strip().lower() not in payment_testers():
        return False
    prov = provider()
    return prov is not None and prov.test_mode


PRELAUNCH_CYCLE = timedelta(days=30)


async def made_at(db: AsyncSession, persona: Persona) -> datetime:
    """대화 상대를 다 만든 날.

    예전에 만든 대화 상대(ready_at 이 없는)는 처음 '만들기'가 끝난 때, 그것도 없으면 처음 만든 때.
    """
    if persona.ready_at is not None:
        return _aware(persona.ready_at)
    first_build = await db.scalar(
        select(func.min(PersonaJob.updated_at)).where(
            PersonaJob.persona_id == persona.id,
            PersonaJob.kind == "BUILD",
            PersonaJob.status == JobStatus.DONE,
        )
    )
    return _aware(first_build or persona.created_at)


def prelaunch_cycle(anchor: datetime, at: datetime) -> tuple[datetime, datetime]:
    """다 만든 날(anchor)부터 30일씩 끊었을 때 at 이 든 30일의 시작과 끝(다시 채워지는 때)."""
    k = max(0, (at - anchor) // PRELAUNCH_CYCLE)
    start = anchor + PRELAUNCH_CYCLE * k
    return start, start + PRELAUNCH_CYCLE


async def _prelaunch(db: AsyncSession, persona: Persona) -> Entitlement:
    """결제 전 모드: 다 만든 대화 상대는 무료로, 한도 없이 쓴다(PRELAUNCH).

    무료 체험 자리(trials_used)는 쓰지 않는다. 결제가 열리면 그때부터 원래 규칙으로 체험이 시작된다.
    PRELAUNCH_CONVERSATIONS 가 있으면 다 만든 날부터 30일마다 끊어, 지금 30일 동안의 대화 수를
    그 상한과 견준다(다음 30일이 시작되면 다시 채워진다).
    """
    limit = get_settings().prelaunch_conversations
    if persona.setup_step != SetupStep.READY:
        # 다 만든 뒤부터 쓴다(만드는 중에는 대화하지 않는다)
        return Entitlement(
            persona.id, "trial", "NOT_STARTED", True, "ivc", None, None, None, limit, 0
        )
    anchor = await made_at(db, persona)
    since, resets = prelaunch_cycle(anchor, now()) if limit is not None else (None, None)
    used = await conversations_since(db, persona.id, since)
    return Entitlement(
        persona.id,
        "trial",
        "PRELAUNCH",
        True,
        "ivc",
        None,
        None,
        None,
        limit,
        used,
        resets_at=resets,
        counted_since=since,
        made_at=anchor,
    )


# ────────────────────────── 구독 신청(결제 전) ──────────────────────────

REQUESTED, CONVERTED = "REQUESTED", "CONVERTED"


async def get_request(db: AsyncSession, persona_id: str) -> SubscriptionRequest | None:
    return await db.scalar(
        select(SubscriptionRequest).where(SubscriptionRequest.persona_id == persona_id)
    )


def request_view(req: SubscriptionRequest | None) -> dict[str, Any] | None:
    """화면에 보일 구독 신청. 신청 중(REQUESTED)인 것만 — 취소·결제한 것은 None."""
    if req is None or req.status != REQUESTED:
        return None
    return {
        "plan": req.plan,
        "planName": plan_name(req.plan),
        "status": req.status,
        "createdAt": _aware(req.created_at),
    }


async def _on_paid(db: AsyncSession, pay_id: str) -> None:
    """결제가 됐다: 이 대화 상대의 구독 신청을 CONVERTED 로 바꾸고 통계를 남긴다.

    따로 연 세션으로 한다(부르는 쪽은 결제 기록을 commit 한 뒤다). 실패해도 결제·구독은 그대로다.
    """
    from app.db.session import SessionLocal
    from app.services import analytics

    try:
        async with SessionLocal() as own:
            pay = await own.get(Payment, pay_id)
            if pay is None:
                return
            if pay.persona_id:
                await own.execute(
                    update(SubscriptionRequest)
                    .where(
                        SubscriptionRequest.persona_id == pay.persona_id,
                        SubscriptionRequest.status == REQUESTED,
                    )
                    .values(status=CONVERTED, updated_at=now())
                )
                await own.commit()
            await analytics.track(
                own,
                "payment_succeeded",
                user_id=pay.user_id,
                persona_id=pay.persona_id,
                plan=pay.plan,
                amount=pay.amount,
                kind=pay.kind,
            )
    except Exception:  # noqa: BLE001
        logger.exception("after-payment bookkeeping failed")


async def _after_payment(pay_id: str | None) -> None:
    """구독 기간을 정한(commit 한) 뒤: 계약 내용(요금제·금액·이용 기간·다음 결제일·청약철회와
    환불 방법)을 앱 알림과 메일로 남긴다(전자상거래법 제13조 제2항 · contract_mail). 실패는 기록만
    한다."""
    if pay_id is None:
        return
    from app.services import contract_mail

    await contract_mail.after_payment(pay_id)


# ────────────────────────── 초기 가족 요금 ──────────────────────────
# 가족(계정) 단위다. 처음 100가족이 첫 결제부터 12개월 동안,
# 그 가족의 모든 페르소나를 초기 요금으로 낸다.


async def _beta_taken(db: AsyncSession) -> int:
    return int(
        await db.scalar(select(func.count(User.id)).where(User.beta_until.is_not(None))) or 0
    )


async def beta_open(db: AsyncSession) -> bool:
    s = get_settings()
    return s.beta_pricing_open and await _beta_taken(db) < s.beta_families


def _beta_active(user: User) -> bool:
    return bool(user.beta_until and _aware(user.beta_until) > now())


def price_for(plan: Plan, user: User) -> int:
    """이 가족이 지금 내는 값. 초기 가족 요금을 받았고 기간 안이면 초기 요금."""
    if plan.beta_price is not None and _beta_active(user):
        return plan.beta_price
    return plan.price


def _beta_offer(plan: Plan, user: User, available: bool) -> bool:
    """이 요금제를 고르면 초기 가족 요금인가.

    받은 가족은 기간 안이면, 처음 결제하는 가족은 자리가 남았으면 초기 요금이다.
    """
    if plan.beta_price is None:
        return False
    if _beta_active(user):
        return True
    return available and user.beta_until is None


async def quote(db: AsyncSession, user: User, plan: Plan) -> tuple[int, bool]:
    """결제 창에 보일 값과 초기 가족 요금인지."""
    beta = _beta_offer(plan, user, await beta_open(db))
    return (plan.beta_price if beta and plan.beta_price is not None else plan.price), beta


def _set_beta(user: User) -> None:
    """이 가족에게 초기 가족 요금 자리를 준다(지금부터 beta_months 개월). 이미 있으면 그대로."""
    if user.beta_until is not None:
        return
    start = now()
    day = kst_day(start)
    end = start
    for _ in range(get_settings().beta_months):
        end = add_month(end, day)
    user.beta_until = end


async def _grant_beta(db: AsyncSession, user: User, plan: Plan) -> None:
    """자리가 남았으면 초기 가족 요금 자리를 준다(한 번만 — 끝난 뒤 다시 받지 않는다)."""
    if user.beta_until is not None or plan.beta_price is None or not await beta_open(db):
        return
    _set_beta(user)


def regular_price_due(user: User) -> bool:
    """초기 가족 요금이 끝나 정식 요금으로 오르는데 아직 동의를 받지 않았나."""
    return bool(user.beta_until and not _beta_active(user) and not user.regular_price_consent_at)


def beta_ends_soon(user: User) -> bool:
    """초기 가족 요금이 30일 안에 끝나거나 이미 끝났다(정식 요금 동의를 물을 때)."""
    end = _aware(user.beta_until)
    return bool(end and end - now() <= timedelta(days=30))


def view(ent: Entitlement) -> dict[str, Any]:
    all_plans = plans()
    return {
        "personaId": ent.persona_id,
        "plan": ent.plan,
        "planName": all_plans[ent.plan].name if ent.plan in all_plans else None,
        "status": ent.status,
        "active": ent.active,
        "voice": ent.voice,
        "trialEndsAt": ent.trial_ends_at,
        "periodEnd": ent.period_end,
        "nextPlan": ent.next_plan,
        "conversationsLimit": ent.conversations_limit,
        "conversationsUsed": ent.conversations_used,
        "conversationsLeft": ent.conversations_left,
        "conversationsResetAt": ent.resets_at,
        # 결제 전 모드: 대화 상대를 다 만든 날(이날부터 30일마다 다시 채워진다)
        "madeAt": ent.made_at,
        # 대화 횟수를 다 써서 지금은 글로만 대답한다(끊지 않는다)
        "textOnly": ent.active and ent.conversations_left == 0,
    }


def plans_view(user: User | None = None, beta_available: bool = False) -> list[dict[str, Any]]:
    """요금제 목록. chargePrice = 이 가족이 지금 고르면 내는 값(페르소나 하나에 한 달)."""
    items = []
    for p in plans().values():
        if user is None:
            beta = p.beta_price is not None and beta_available
        else:
            beta = _beta_offer(p, user, beta_available)
        items.append(
            {
                "id": p.id,
                "name": p.name,
                "price": p.price,
                "betaPrice": p.beta_price,
                "chargePrice": p.beta_price if beta and p.beta_price is not None else p.price,
                "beta": beta,
                "voice": p.voice,
                "conversations": p.conversations,
                "summary": p.summary,
            }
        )
    return items


# ────────────────────────── 결제 회사 ──────────────────────────


@dataclass
class ChargeResult:
    ok: bool
    payment_key: str | None = None
    receipt_url: str | None = None
    approved_at: datetime | None = None
    failure: str | None = None
    # 연결이 끊겨 결제됐는지 모른다. 실패로 치고 새 주문으로 다시 결제하면 두 번 결제될 수 있다.
    pending: bool = False
    # 이 결제를 남긴 결제 기록(Payment.id). _charge 가 채운다(계약 내용 알림에 쓴다).
    pay_id: str | None = None


class BillingUnknown(BillingError):
    """결제 회사에 요청은 갔을 수도 있는데 답을 못 받았다(시간 초과·연결 끊김·5xx)."""


class Provider(Protocol):
    name: str
    test_mode: bool

    def checkout_html(
        self,
        *,
        customer_key: str,
        plan: Plan,
        amount: int,
        beta: bool,
        success_url: str,
        fail_url: str,
        customer_name: str,
        customer_email: str | None,
        beta_until: datetime | None = None,
    ) -> str: ...

    async def issue_billing_key(
        self, *, auth_key: str, customer_key: str
    ) -> tuple[str, str | None]: ...

    async def charge(
        self,
        *,
        billing_key: str,
        customer_key: str,
        amount: int,
        order_id: str,
        order_name: str,
        customer_name: str,
        customer_email: str | None,
    ) -> ChargeResult: ...

    async def lookup(self, order_id: str) -> ChargeResult | None:
        """주문 번호로 결제 결과를 묻는다. 그런 결제가 없으면 None, 여전히 모르면 pending."""
        ...


_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TalkTo 결제</title>
<style>
body{{margin:0;background:#F4F1EA;color:#22251F;font-family:-apple-system,BlinkMacSystemFont,
'Apple SD Gothic Neo','Noto Sans KR',sans-serif;display:flex;min-height:100vh;align-items:center;
justify-content:center}}
.card{{background:#FCFBF7;border:1px solid #E3DED3;border-radius:20px;padding:32px 28px;
max-width:380px;width:calc(100% - 32px);box-sizing:border-box}}
h1{{font-size:20px;margin:0 0 6px}} p{{color:#50544B;line-height:1.6;font-size:15px;margin:6px 0}}
.price{{font-size:28px;font-weight:800;margin:14px 0 4px}}
button{{width:100%;height:52px;border:0;border-radius:999px;background:#2E5A48;color:#fff;
font-size:16px;font-weight:700;margin-top:18px;cursor:pointer}}
button.sub{{background:transparent;color:#50544B;border:1px solid #CFC9BC}}
.note{{font-size:13px;color:#878B80}}
.withdrawal{{font-size:13px;line-height:1.6;background:#F4F1EA;border-radius:12px;padding:12px 14px;
margin:14px 0 0}}
.biz{{font-size:12px;color:#878B80;line-height:1.6;margin:22px 0 0}}
.biz a{{color:#2E5A48;font-weight:700}}
</style></head><body><div class="card">{body}</div>{script}</body></html>"""


def _js(value: Any) -> str:
    """<script> 안에 넣는 값. json 은 '</' 를 막지 못해 이름에 </script> 가 들어가면 스크립트가
    풀린다. HTML 에 의미 있는 글자는 \\u 로 바꾼다(자바스크립트는 같은 글자로 읽는다)."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def _price_html(plan: Plan, amount: int, beta: bool, beta_until: datetime | None = None) -> str:
    html = f'<div class="price">월 {amount:,}원</div>'
    if beta and amount != plan.price:
        end = _aware(beta_until)
        if end:
            k = end.astimezone(KST)
            until = f"{k.year}년 {k.month}월 {k.day}일까지"
        else:
            until = f"{get_settings().beta_months}개월 동안"
        html += (
            f'<p class="note">첫 {get_settings().beta_families}가족 할인가 · '
            f"{until} 이 값으로 결제돼요. 원래 가격은 월 {plan.price:,}원이에요.</p>"
        )
    return html


def _withdrawal_html() -> str:
    """결제 단추 위에 한 번 더 보이는 청약철회 안내(WO 1-8 · legal.PAYMENT_NOTICE)."""
    from app.services import legal

    return f'<p class="withdrawal">{escape(legal.PAYMENT_NOTICE)}</p>'


def _business_html() -> str:
    """결제 화면 아래쪽 사업자 정보 한 줄과 개인정보 처리방침(전자상거래법 제10조 · WO 6).

    값은 설정(legal.business)에서만 온다. 비운 값은 빼고, 모두 HTML 이스케이프한다.
    """
    from app.services import legal

    b = legal.business()
    items = (
        ("상호", b["name"]),
        ("대표", b["owner"]),
        ("사업장 주소", b["address"]),
        ("전화", b["phone"]),
        ("이메일", b["email"]),
        ("사업자등록번호", b["regNo"]),
        ("통신판매업 신고번호", b["mailOrderNo"]),
        ("호스팅", b["hosting"]),
    )
    line = " · ".join(f"{label} {escape(value)}" for label, value in items if value)
    privacy = escape(app_url("/legal/privacy_policy"))
    return (
        f'<p class="biz">{line}<br><a href="{privacy}" target="_blank" rel="noopener">'
        "개인정보 처리방침</a></p>"
    )


class MockProvider:
    """로컬 시험 결제. 카드도 받지 않고 돈도 오가지 않는다. 화면 흐름만 확인한다."""

    name = "mock"
    test_mode = True

    def checkout_html(
        self,
        *,
        customer_key,
        plan,
        amount,
        beta,
        success_url,
        fail_url,
        customer_name,
        customer_email,
        beta_until=None,
    ) -> str:
        ok = f"{success_url}&customerKey={customer_key}&authKey=mock_{secrets.token_hex(8)}"
        fail = f"{fail_url}&code=USER_CANCEL&message=" + "결제를 취소했어요."
        body = (
            f"<h1>{escape(plan.name)} 요금제 · 연습용 결제</h1>"
            + _price_html(plan, amount, beta, beta_until)
            + "<p>연습용 결제라 실제로 돈이 나가지 않아요. 카드도 받지 않아요.</p>"
            + _withdrawal_html()
            + f"<button onclick=\"location.href='{escape(ok)}'\">연습으로 결제하기</button>"
            f'<button class="sub" onclick="location.href=\'{escape(fail)}\'">취소</button>'
            + _business_html()
        )
        return _PAGE.format(body=body, script="")

    async def issue_billing_key(self, *, auth_key, customer_key):
        if not auth_key.startswith("mock_"):
            raise BillingError("카드 등록에 실패했어요.")
        return f"mock_billing_{secrets.token_hex(8)}", "연습용 카드"

    async def charge(
        self,
        *,
        billing_key,
        customer_key,
        amount,
        order_id,
        order_name,
        customer_name,
        customer_email,
    ) -> ChargeResult:
        if billing_key.startswith("mock_fail"):
            return ChargeResult(False, failure="연습용 카드 결제가 거절됐어요.")
        return ChargeResult(True, payment_key=f"mock_{order_id}", approved_at=now())

    async def lookup(self, order_id: str) -> ChargeResult | None:
        return None


class TossProvider:
    """토스페이먼츠 자동결제(빌링). docs.tosspayments.com — 카드 등록은 토스 결제창이 받는다."""

    name = "toss"
    API = "https://api.tosspayments.com"

    def __init__(self, client_key: str, secret_key: str) -> None:
        self.client_key = client_key
        self.secret_key = secret_key
        self.test_mode = client_key.startswith("test_")

    def _auth(self) -> dict[str, str]:
        token = base64.b64encode(f"{self.secret_key}:".encode()).decode()
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def checkout_html(
        self,
        *,
        customer_key,
        plan,
        amount,
        beta,
        success_url,
        fail_url,
        customer_name,
        customer_email,
        beta_until=None,
    ) -> str:
        body = (
            f"<h1>{escape(plan.name)} 요금제 구독</h1>"
            + _price_html(plan, amount, beta, beta_until)
            + "<p>카드를 등록하면 지금 첫 달이 결제되고, 매달 같은 날 자동으로 결제돼요.</p>"
            '<p class="note">카드 정보는 토스페이먼츠 결제 화면에서 입력하고, '
            "TalkTo에는 저장되지 않아요.</p>"
            + _withdrawal_html()
            + '<button id="pay">카드 등록하고 결제하기</button>'
            + (
                '<p class="note">연습용 결제라 실제로 돈이 나가지 않아요.</p>'
                if self.test_mode
                else ""
            )
            + _business_html()
        )
        options = {
            "method": "CARD",
            "successUrl": success_url,
            "failUrl": fail_url,
            "customerName": customer_name,
        }
        if customer_email:
            options["customerEmail"] = customer_email
        script = (
            '<script src="https://js.tosspayments.com/v2/standard"></script><script>'
            f"const toss = TossPayments({_js(self.client_key)});"
            f"const payment = toss.payment({{customerKey: {_js(customer_key)}}});"
            "async function go(){try{await payment.requestBillingAuth("
            f"{_js(options)});}}catch(e){{"
            f"location.href={_js(fail_url)}+'&code='+encodeURIComponent(e.code||'')"
            "+'&message='+encodeURIComponent(e.message||'');}}}"
            "document.getElementById('pay').onclick=go;go();</script>"
        )
        return _PAGE.format(body=body, script=script)

    async def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        headers = self._auth()
        if idempotency_key:
            # 같은 결제를 같은 열쇠로 다시 보내면 토스가 처음 결과를 그대로 돌려준다(15일 유효)
            headers["Idempotency-Key"] = idempotency_key
        # 토스 문서: 결제 승인은 60초 이상 기다린다
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.request(
                    method, f"{self.API}{path}", json=body, headers=headers
                )
        except httpx.HTTPError as error:
            logger.warning("toss request failed %s: %s", path, error)
            raise BillingUnknown(
                "결제 회사와 연결하지 못했어요. 잠시 뒤 다시 해 주세요."
            ) from error
        data = response.json() if response.content else {}
        if response.status_code >= 500:
            raise BillingUnknown("결제 회사가 잠시 답하지 않아요. 결제 결과를 확인하고 있어요.")
        if response.status_code >= 400:
            error = BillingError(data.get("message") or "결제 회사와 연결하지 못했어요.")
            error.code = data.get("code")  # type: ignore[attr-defined]
            raise error
        return data

    async def _post(self, path: str, body: dict, *, idempotency_key: str | None = None) -> dict:
        return await self._request("POST", path, body, idempotency_key=idempotency_key)

    async def issue_billing_key(self, *, auth_key, customer_key):
        data = await self._post(
            "/v1/billing/authorizations/issue", {"authKey": auth_key, "customerKey": customer_key}
        )
        card = data.get("card") or {}
        label = " ".join(x for x in [data.get("cardCompany"), card.get("number")] if x) or None
        return data["billingKey"], label

    @staticmethod
    def _result(data: dict) -> ChargeResult:
        if data.get("status") != "DONE":
            # 결제 회사의 상태 값(ABORTED 등)은 로그로만 남긴다
            logger.warning("toss payment not done: %s", data.get("status"))
            return ChargeResult(False, failure="결제가 끝나지 않았어요. 잠시 뒤 다시 해 주세요.")
        approved = data.get("approvedAt")
        return ChargeResult(
            True,
            payment_key=data.get("paymentKey"),
            receipt_url=(data.get("receipt") or {}).get("url"),
            # UTC 로 맞춘다(SQLite 는 시간대를 지우고 저장해 +09:00 그대로면 다른 시각과 어긋난다)
            approved_at=datetime.fromisoformat(approved).astimezone(UTC) if approved else now(),
        )

    async def charge(
        self,
        *,
        billing_key,
        customer_key,
        amount,
        order_id,
        order_name,
        customer_name,
        customer_email,
    ) -> ChargeResult:
        body = {
            "customerKey": customer_key,
            "amount": amount,
            "orderId": order_id,
            "orderName": order_name,
            "customerName": customer_name,
        }
        if customer_email:
            body["customerEmail"] = customer_email
        path = f"/v1/billing/{billing_key}"
        for _ in range(2):  # 답을 못 받으면 같은 주문·같은 열쇠로 한 번 더(두 번 결제되지 않는다)
            try:
                return self._result(await self._post(path, body, idempotency_key=order_id))
            except BillingUnknown:
                continue
            except BillingError as error:
                return ChargeResult(False, failure=str(error))
        found = await self.lookup(order_id)
        if found is None:
            return ChargeResult(False, failure="결제 회사와 연결하지 못했어요.")
        return found

    async def lookup(self, order_id: str) -> ChargeResult | None:
        try:
            data = await self._request("GET", f"/v1/payments/orders/{order_id}")
        except BillingUnknown:
            return ChargeResult(False, pending=True, failure="결제 결과를 확인하고 있어요.")
        except BillingError as error:
            if getattr(error, "code", None) == "NOT_FOUND_PAYMENT":
                return None
            return ChargeResult(False, pending=True, failure="결제 결과를 확인하고 있어요.")
        return self._result(data)


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def provider() -> Provider | None:
    """키가 있으면 토스. 키가 없으면 이 서버가 내 컴퓨터(localhost)일 때만 시험 결제를 연다.

    배포한 서버에 키를 아직 안 넣었다고 시험 결제(무료로 구독이 켜짐)가 열리면 안 된다.
    배포 서버는 PUBLIC_BASE_URL 이 실제 주소라 여기서 막힌다.
    """
    s = get_settings()
    if s.toss_client_key and s.toss_secret_key:
        return TossProvider(s.toss_client_key.strip(), s.toss_secret_key.strip())
    host = (urlparse(s.public_base_url).hostname or "").lower()
    local = host in _LOCAL_HOSTS or host.endswith(".localhost") or host == "testserver"
    if s.environment in ("local", "development", "test") and local:
        return MockProvider()
    return None


# ────────────────────────── 구독 바꾸기 ──────────────────────────


def kst_day(at: datetime) -> int:
    return at.astimezone(KST).day


def add_month(at: datetime, day: int | None = None) -> datetime:
    """한 달 뒤 결제일(한국 날짜). day 가 31이면 짧은 달은 그달 마지막 날, 그다음 달은 다시 31일."""
    k = at.astimezone(KST)
    year, month = (k.year + 1, 1) if k.month == 12 else (k.year, k.month + 1)
    d = min(day or k.day, calendar.monthrange(year, month)[1])
    return k.replace(year=year, month=month, day=d).astimezone(UTC)


def _order_id() -> str:
    return f"talkto_{now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}"


async def _charge(
    db: AsyncSession,
    user: User,
    sub: Subscription,
    plan: Plan,
    amount: int,
    order_name: str,
    *,
    kind: str,
) -> ChargeResult:
    prov = provider()
    if prov is None or prov.name != sub.provider or not sub.billing_key:
        return ChargeResult(False, failure=NOT_OPEN)
    order_id = _order_id()
    billing_key, customer_key = sub.billing_key, sub.customer_key
    # 첫 결제면 그 대화 상대가 무료 체험을 실제로 썼는지 지금 남긴다(청약철회 판단 — 대화 상대를
    # 지워도 결제 기록에 남는다)
    trial_used = await trial_used_before(db, sub.persona_id, now()) if kind == "first" else None
    # 결제 회사에 보내기 전에 '결과 모름'으로 기록부터 남긴다. 도중에 페르소나가 지워지거나 서버가
    # 멈춰도 결제 기록은 남고, settle_unknown 이 같은 주문 번호로 물어 정한다.
    pay = Payment(
        user_id=user.id,
        persona_id=sub.persona_id,
        plan=plan.id,
        provider=sub.provider,
        order_id=order_id,
        amount=amount,
        kind=kind,
        status="UNKNOWN",
        trial_used=trial_used,
    )
    db.add(pay)
    await db.commit()
    pay_id = pay.id
    result = await prov.charge(
        billing_key=billing_key,
        customer_key=customer_key,
        amount=amount,
        order_id=order_id,
        order_name=order_name,
        customer_name=user.name,
        customer_email=None if user.email.endswith(".invalid") else user.email,
    )
    # 결과도 바로 따로 남긴다(뒤이어 구독을 고치다 실패해도 결제 기록은 지켜지게)
    await db.execute(
        update(Payment)
        .where(Payment.id == pay_id)
        .values(
            status="DONE" if result.ok else ("UNKNOWN" if result.pending else "FAILED"),
            payment_key=result.payment_key,
            receipt_url=result.receipt_url,
            failure=result.failure,
            approved_at=result.approved_at,
        )
    )
    await db.commit()
    result.pay_id = pay_id
    if result.ok:
        await _on_paid(db, pay_id)
    return result


async def _claim(db: AsyncSession, sub: Subscription, minutes: int = 10) -> bool:
    """이 구독을 잠깐 잡는다. 이미 다른 곳(갱신·결제 창·요금제 바꾸기)이 잡았으면 False.

    SQLite·PostgreSQL 모두 되는 조건부 UPDATE 다(같은 행을 두 곳이 동시에 잡지 못한다).
    """
    current = now()
    result = await db.execute(
        update(Subscription)
        .where(
            Subscription.id == sub.id,
            or_(Subscription.renew_lock_until.is_(None), Subscription.renew_lock_until < current),
        )
        .values(renew_lock_until=current + timedelta(minutes=minutes))
    )
    await db.commit()
    if result.rowcount != 1:
        return False
    await db.refresh(sub)
    return True


async def _release(db: AsyncSession, sub: Subscription) -> None:
    """잡아 둔 구독을 놓는다. 앞 단계가 실패해(페르소나를 그사이 지움 등) 세션이 깨졌어도 놓는다."""
    sub_id = sa_inspect(sub).identity[0]
    try:
        if not db.is_active:
            await db.rollback()
        await db.execute(
            update(Subscription).where(Subscription.id == sub_id).values(renew_lock_until=None)
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — 자물쇠는 10분 뒤 저절로 풀린다
        logger.exception("subscription unlock failed %s", sub_id)
        await db.rollback()


async def pending_for_persona(db: AsyncSession, persona_id: str) -> bool:
    """이 페르소나에 결과를 모르는 결제가 있나(정해질 때까지 새로 결제·지우기를 하지 않는다)."""
    return (
        await db.scalar(
            select(Payment.id)
            .where(Payment.persona_id == persona_id, Payment.status == "UNKNOWN")
            .limit(1)
        )
    ) is not None


async def _note_regular_price(db: AsyncSession, user: User, plan: Plan) -> None:
    """초기 가족 요금이 끝난 가족이 정식 요금을 보고 결제했으면, 정식 요금 동의로 남긴다.

    초기 요금이 있는 요금제만 센다(설정으로 초기 요금을 비운 요금제는 원래 한 가지 값이라, 그걸
    샀다고 다른 페르소나의 요금이 오르는 데 동의한 것은 아니다).
    """
    if plan.beta_price is None:
        return
    if user.beta_until is None or _beta_active(user) or user.regular_price_consent_at:
        return
    from app.services import legal

    user.regular_price_consent_at = now()
    # 지금 상태(consents)와 이력(consent_records) 둘 다 남긴다
    await legal.record(db, user, "regular_price", True, doc="payment")


async def ensure_customer_key(db: AsyncSession, user: User) -> str:
    """결제 회사에 넘기는 고객 번호(가족마다 하나). 짐작되는 값(이메일·id)을 쓰지 않는다."""
    for sub in await user_subscriptions(db, user.id):
        if sub.customer_key:
            return sub.customer_key
    return f"tt_{secrets.token_urlsafe(18)}"


async def card_on_file(db: AsyncSession, user: User) -> Subscription | None:
    """이 가족이 등록해 둔 카드(다른 페르소나 구독의 빌링키). 결제가 밀린 카드는 쓰지 않는다."""
    prov = provider()
    if prov is None:
        return None
    subs = [
        s
        for s in await user_subscriptions(db, user.id)
        if s.billing_key and s.provider == prov.name and s.status == ACTIVE
    ]
    subs.sort(key=lambda s: _aware(s.updated_at) or now(), reverse=True)
    return subs[0] if subs else None


def _start_period(sub: Subscription, plan_id: str, start: datetime) -> None:
    sub.plan, sub.status = plan_id, ACTIVE
    sub.billing_day = kst_day(start)
    sub.current_period_start, sub.current_period_end = start, add_month(start, sub.billing_day)
    sub.cancel_at_period_end, sub.next_plan, sub.failed_attempts = False, None, 0


def _current(sub: Subscription) -> bool:
    """이번 기간 값을 이미 냈고 기간 안에 있는가."""
    return (
        sub.status == ACTIVE
        and sub.current_period_end is not None
        and _aware(sub.current_period_end) > now()
    )


async def _subscription_for(
    db: AsyncSession, user: User, persona: Persona, provider_name: str, customer_key: str
) -> Subscription:
    sub = await get_subscription(db, persona.id)
    if sub is None:
        sub = Subscription(
            user_id=user.id,
            persona_id=persona.id,
            plan="memories",
            provider=provider_name,
            customer_key=customer_key,
            status=CANCELED,
        )
        db.add(sub)
        await db.commit()
    return sub


async def _first_month(
    db: AsyncSession,
    user: User,
    sub: Subscription,
    plan: Plan,
    quoted: tuple[int, bool] | None = None,
) -> int:
    """첫 달을 결제하고 기간을 연다. 낸 값을 돌려준다(실패하면 BillingError).

    quoted = 결제 창에 보여 준 (값, 초기 요금인지). 그사이 자리가 다 차도 보여 준 값으로 받는다.
    """
    if quoted is not None:
        amount, beta = quoted
    else:
        beta = _beta_offer(plan, user, await beta_open(db))
        amount = plan.beta_price if beta and plan.beta_price is not None else plan.price
    result = await _charge(
        db, user, sub, plan, amount, f"TalkTo {plan.name} 요금제 1개월", kind="first"
    )
    if beta and (result.ok or result.pending):
        # 초기 요금으로 받았으면 자리를 바로 준다(결과 확인 중이어도 — 나중에 실패해 다시 결제해도
        # 같은 값). 여기서 자리가 남았는지 다시 물으면 낸 값과 갱신 값이 달라질 수 있다.
        _set_beta(user)
    elif result.ok or result.pending:
        await _note_regular_price(db, user, plan)
    if not result.ok:
        await db.commit()
        raise BillingError(
            "결제 결과를 확인하고 있어요. 몇 분 뒤 요금제 화면을 다시 봐 주세요."
            if result.pending
            else (result.failure or "결제하지 못했어요.")
        )
    # 승인된 때부터 한 달(settle_unknown 과 같게, 지금보다 늦게는 열지 않는다). 다시 묻거나 조회로
    # 정한 결과는 승인이 몇 분 앞설 수 있다 — 지금부터 열면 그 결제가 이번 기간 밖이 되어
    # 결제 후 첫 대화·일할 환불에서 빠진다
    current = now()
    _start_period(sub, plan.id, min(_aware(result.approved_at) or current, current))
    await db.commit()
    await _after_payment(result.pay_id)
    return amount


async def activate(
    db: AsyncSession,
    user: User,
    persona: Persona,
    plan_id: str,
    *,
    auth_key: str,
    customer_key: str,
    quoted: tuple[int, bool] | None = None,
) -> Subscription:
    """카드 등록을 마치고 돌아오면: 빌링키를 받고, 이 페르소나의 첫 달을 결제하고, 구독을 켠다.

    이미 이번 기간 값을 낸 구독이면(결제 창을 두 개 열어 둘 다 끝낸 경우 등) 카드만 바꾸고
    한 달 값을 다시 받지 않는다. 요금제가 다르면 올리기·내리기 규칙을 따른다.
    """
    plan = plans()[plan_id]
    prov = provider()
    if prov is None:
        raise BillingError(NOT_OPEN)
    # 결과를 모르는 결제는 대화 상대마다 본다(다른 대화 상대의 결제가 이 결제를 막지 않는다)
    if await pending_for_persona(db, persona.id):
        raise BillingError(f"{CHECKING} 몇 분 뒤 요금제 화면을 다시 봐 주세요.")
    billing_key, card_label = await prov.issue_billing_key(
        auth_key=auth_key, customer_key=customer_key
    )
    sub = await _subscription_for(db, user, persona, prov.name, customer_key)
    if not await _claim(db, sub):
        raise BillingError("결제를 처리하고 있어요. 잠시 뒤 요금제 화면을 다시 봐 주세요.")
    try:
        already = _current(sub) and sub.provider == prov.name
        sub.provider, sub.customer_key = prov.name, customer_key
        sub.billing_key, sub.card_label = billing_key, card_label
        await db.commit()
        if already:
            if plan_id != sub.plan:
                await _change(db, user, sub, plan_id)
            return sub
        await _first_month(db, user, sub, plan, quoted)
        return sub
    finally:
        await _release(db, sub)


def first_month_amount(plan: Plan, user: User, available: bool) -> int:
    beta = _beta_offer(plan, user, available)
    return plan.beta_price if beta and plan.beta_price is not None else plan.price


class PriceChanged(BillingError):
    """화면에 보여 준 값보다 많이 받게 됐다(초기 요금 자리가 다 찼거나 끝남). 다시 보여 준다."""


async def start_with_card(
    db: AsyncSession,
    user: User,
    persona: Persona,
    plan_id: str,
    expected_amount: int | None = None,
) -> dict[str, Any]:
    """다른 페르소나에 등록해 둔 카드로 이 페르소나 구독을 바로 시작한다(카드를 다시 받지 않는다).

    등록된 카드가 없거나 이 페르소나 결제가 밀려 있으면 {"needsCheckout": True}
    — 결제 창에서 카드를 받는다.
    """
    card = await card_on_file(db, user)
    existing = await get_subscription(db, persona.id)
    if card is None or (existing is not None and existing.status == PAST_DUE):
        return {"needsCheckout": True}
    if await pending_for_persona(db, persona.id):
        raise BillingError(f"{CHECKING} 몇 분 뒤 다시 해 주세요.")
    plan = plans()[plan_id]
    amount = first_month_amount(plan, user, await beta_open(db))
    if expected_amount is not None and amount > expected_amount:
        raise PriceChanged("요금이 바뀌었어요. 다시 확인해 주세요.")
    sub = await _subscription_for(db, user, persona, card.provider, card.customer_key)
    if not await _claim(db, sub):
        raise BillingError("결제를 처리하고 있어요. 잠시 뒤 다시 해 주세요.")
    try:
        if _current(sub):
            # 그사이 다른 곳에서 이미 시작했다
            return await _change(db, user, sub, plan_id, expected_amount)
        sub.provider, sub.customer_key = card.provider, card.customer_key
        sub.billing_key, sub.card_label = card.billing_key, card.card_label
        await db.commit()
        beta = plan.beta_price is not None and amount == plan.beta_price != plan.price
        charged = await _first_month(db, user, sub, plan, (amount, beta))
        return {"changed": True, "started": True, "charged": charged}
    finally:
        await _release(db, sub)


async def change_plan(
    db: AsyncSession,
    user: User,
    persona: Persona,
    plan_id: str,
    expected_amount: int | None = None,
) -> dict[str, Any]:
    """이 페르소나 구독의 요금제를 바꾼다.

    올리기(추억 → 가족)는 바로 바꾸고, 이번 기간에 남은 날만큼 차액을 결제한다.
    내리기는 이번 기간이 끝날 때 바꾼다(남은 기간은 지금 요금제 그대로).
    결제가 밀린(PAST_DUE)·끝난 구독은 {"needsCheckout": True} — 결제 창에서 카드를 다시 받는다.
    """
    sub = await get_subscription(db, persona.id)
    if sub is None or not sub.billing_key or sub.status != ACTIVE:
        return {"needsCheckout": True}
    if not _current(sub):
        # 이번 결제일이 지났고 갱신이 곧 돈다. 여기서 또 결제하면 갱신과 겹친다.
        raise BillingError("이번 달 결제를 마무리하는 중이에요. 잠시 뒤 다시 해 주세요.")
    if not await _claim(db, sub):
        raise BillingError("결제를 처리하고 있어요. 잠시 뒤 다시 해 주세요.")
    try:
        return await _change(db, user, sub, plan_id, expected_amount)
    finally:
        await _release(db, sub)


def upgrade_amount(sub: Subscription, user: User, target: Plan, beta_available: bool) -> int | None:
    """올리기 차액(이번 기간에 남은 날만큼). 내리기·같은 요금제면 None. _change 와 같은 셈이다."""
    all_plans = plans()
    current = all_plans[sub.plan]
    if target.rank <= current.rank or not sub.current_period_start or not sub.current_period_end:
        return None
    paid_current = price_for(current, user)
    if target.beta_price is not None and user.beta_until is None and beta_available:
        target_price = target.beta_price  # 바꿀 때 초기 요금 자리를 준다
    else:
        target_price = price_for(target, user)
    start, end = _aware(sub.current_period_start), _aware(sub.current_period_end)
    remaining = max(0.0, (end - now()).total_seconds()) / max(1.0, (end - start).total_seconds())
    gap = max(0, target_price - paid_current)
    return max(100, int(round(gap * remaining / 10.0)) * 10)


async def _change(
    db: AsyncSession,
    user: User,
    sub: Subscription,
    plan_id: str,
    expected_amount: int | None = None,
) -> dict[str, Any]:
    all_plans = plans()
    target = all_plans[plan_id]
    current = all_plans[sub.plan]
    if await pending_for_persona(db, sub.persona_id):
        # 앞선 결제(올리기 차액 등)의 결과를 모르는데 또 결제하면 두 번 받을 수 있다
        raise BillingError(f"{CHECKING} 몇 분 뒤 다시 해 주세요.")
    if plan_id == sub.plan:
        sub.next_plan, sub.cancel_at_period_end = None, False
        await db.commit()
        return {"changed": False}
    if target.rank < current.rank:
        # 내리기는 다음 결제부터. 화면은 '자리가 남았으면 초기 요금'으로 보여 준다(plans_view).
        await _grant_beta(db, user, target)
        # 초기 요금이 끝난 가족이 정식 요금을 보고 바꾸면 정식 요금 동의다
        await _note_regular_price(db, user, target)
        sub.next_plan, sub.cancel_at_period_end = plan_id, False
        await db.commit()
        return {"changed": False, "scheduled": plan_id}
    available = await beta_open(db)
    amount = upgrade_amount(sub, user, target, available)
    if expected_amount is not None and amount is not None and amount > expected_amount:
        raise PriceChanged("요금이 바뀌었어요. 다시 확인해 주세요.")
    # 차액을 초기 요금으로 셌는가(upgrade_amount 와 같은 규칙)
    beta = target.beta_price is not None and user.beta_until is None and available
    result = await _charge(
        db,
        user,
        sub,
        target,
        amount,
        f"TalkTo {target.name} 요금제로 바꾸며 남은 날만큼 더 내는 금액",
        kind="upgrade",
    )
    # 자리·동의는 결제가 된 뒤에만 남긴다(카드가 거절되면 아무것도 바뀌지 않는다).
    # 초기 요금으로 받았으면 그사이 자리가 다 찼어도 자리를 준다(낸 값과 갱신 값이 같게).
    if beta and (result.ok or result.pending):
        _set_beta(user)
    elif result.ok or result.pending:
        await _note_regular_price(db, user, target)
    if not result.ok:
        await db.commit()
        raise BillingError(
            "결제 결과를 확인하고 있어요. 몇 분 뒤 다시 봐 주세요."
            if result.pending
            else (result.failure or "결제하지 못했어요.")
        )
    sub.plan, sub.next_plan, sub.cancel_at_period_end = plan_id, None, False
    await db.commit()
    await _after_payment(result.pay_id)
    return {"changed": True, "charged": amount}


async def cancel(db: AsyncSession, persona: Persona) -> None:
    sub = await get_subscription(db, persona.id)
    if sub is None or sub.status == CANCELED:
        raise BillingError("해지할 구독이 없어요.")
    # 해지하면 예약해 둔 요금제 바꾸기도 지운다(해지 예정인데 '다음 결제부터 바뀌어요'가 남지 않게)
    sub.cancel_at_period_end, sub.next_plan = True, None
    await db.commit()


async def resume(db: AsyncSession, persona: Persona) -> None:
    sub = await get_subscription(db, persona.id)
    if sub is None or sub.status == CANCELED:
        raise BillingError("다시 시작할 구독이 없어요. 요금제를 골라 주세요.")
    sub.cancel_at_period_end = False
    await db.commit()


def _live_plan(plan_id: str) -> str:
    """구독에 적을 요금제. 없앤 요금제(premium)의 결제였으면 가장 높은 요금제로 적는다."""
    return plan_id if plan_id in PAID_PLANS else TOP_PLAN


async def settle_unknown(db: AsyncSession) -> int:
    """결과를 모르는 결제(UNKNOWN)를 같은 주문 번호로 결제 회사에 물어 정한다.

    결제된 것으로 나오면 그 결제가 하려던 일(첫 달 켜기·올리기·갱신)을 마저 한다. 결제 기록의
    plan 은 그때 낸 그대로 두고, 구독에는 지금 있는 요금제만 적는다(없앤 요금제가 되살아나지 않게).
    """
    prov = provider()
    if prov is None:
        return 0
    rows = (
        await db.scalars(
            select(Payment).where(
                Payment.status == "UNKNOWN",
                Payment.provider == prov.name,
                Payment.created_at < now() - timedelta(minutes=2),
            )
        )
    ).all()
    settled = 0
    for pay in rows:
        found = await prov.lookup(pay.order_id)
        if found is not None and found.pending:
            continue
        settled += 1
        if found is None or not found.ok:
            pay.status, pay.failure = "FAILED", (found.failure if found else "결제되지 않았어요.")
            await db.commit()
            continue
        pay.status, pay.payment_key = "DONE", found.payment_key
        pay.receipt_url, pay.approved_at = found.receipt_url, found.approved_at
        # 페르소나를 그사이 지웠으면 구독도 없다(결제 기록만 남긴다)
        sub = await get_subscription(db, pay.persona_id) if pay.persona_id else None
        plan_id = _live_plan(pay.plan)
        if sub is not None:
            if pay.kind == "upgrade":
                sub.plan, sub.next_plan = plan_id, None
            else:  # first · renew: 결제된 때부터 한 달
                # 결과를 기다리는 사이 사용자가 해지했거나 내리기를 예약했으면 그대로 둔다
                canceling, next_plan = sub.cancel_at_period_end, sub.next_plan
                _start_period(sub, plan_id, _aware(found.approved_at) or now())
                sub.cancel_at_period_end = canceling
                sub.next_plan = next_plan if next_plan != plan_id else None
        await db.commit()
        await _on_paid(db, pay.id)
        await _after_payment(pay.id)
    return settled


async def renew_due(db: AsyncSession) -> int:
    """기간이 끝난 구독을 갱신(결제)하거나 끝낸다. 몇 개를 다뤘는지 돌려준다."""
    current = now()
    subs = (
        await db.scalars(
            select(Subscription).where(
                Subscription.status.in_([ACTIVE, PAST_DUE]),
                Subscription.current_period_end <= current,
            )
        )
    ).all()
    handled = 0
    all_plans = plans()
    for sub in subs:
        if not await _claim(db, sub):
            continue
        try:
            # 잡는 사이 다른 곳이 이미 갱신했을 수 있다
            if sub.status not in (ACTIVE, PAST_DUE) or _aware(sub.current_period_end) > current:
                continue
            if await pending_for_persona(db, sub.persona_id):
                continue  # 이 대화 상대의 결과를 모르는 결제가 정해질 때까지 새로 결제하지 않는다
            handled += await _renew_one(db, sub, all_plans, current)
        except Exception:  # noqa: BLE001 — 하나가 실패해도(그사이 페르소나를 지움 등) 나머지는 한다
            logger.exception("renewal failed for subscription")
            await db.rollback()
        finally:
            await _release(db, sub)
    return handled


async def _renew_one(db: AsyncSession, sub: Subscription, all_plans, current) -> int:
    user = await db.get(User, sub.user_id)
    end = _aware(sub.current_period_end)
    if sub.cancel_at_period_end:
        sub.status = CANCELED
        await db.commit()
        return 1
    if sub.status == PAST_DUE:
        last = await db.scalar(
            select(Payment)
            .where(Payment.user_id == user.id, Payment.persona_id == sub.persona_id)
            .order_by(Payment.created_at.desc())
            .limit(1)
        )
        if last and _aware(last.created_at) > current - timedelta(hours=RETRY_HOURS):
            if current > end + timedelta(days=GRACE_DAYS):
                sub.status = CANCELED
                await db.commit()
                return 1
            return 0
    plan = all_plans[sub.next_plan or sub.plan]
    if plan.beta_price is not None and regular_price_due(user):
        # 초기 가족 요금 → 정식 요금은 정기결제 가격 인상이다(사전 고지·동의, 마스터 리포트 0.0.5).
        # 동의하지 않았으면 오른 값으로 결제하지 않고 구독을 끝낸다(요금제 화면에서 다시 고른다).
        sub.status = CANCELED
        await db.commit()
        return 1
    result = await _charge(
        db, user, sub, plan, price_for(plan, user), f"TalkTo {plan.name} 요금제 1개월", kind="renew"
    )
    if result.ok:
        # 며칠 안(결제 실패 뒤 다시 된 경우 포함)이면 원래 결제일을 잇는다(밀린 동안도 썼다).
        # 그보다 늦었으면(서버가 오래 멈춘 뒤) 오늘부터 새 기간이다. 원래 날짜를 고집하면 못 쓴
        # 지난 달들을 연달아 결제하게 된다.
        if current - end < timedelta(days=GRACE_DAYS + 1):
            start = end
            sub.plan, sub.next_plan, sub.status, sub.failed_attempts = plan.id, None, ACTIVE, 0
            sub.billing_day = sub.billing_day or kst_day(end)
            sub.current_period_start = start
            sub.current_period_end = add_month(start, sub.billing_day)
        else:
            _start_period(sub, plan.id, current)
        await db.commit()
        await _after_payment(result.pay_id)
    elif result.pending:
        await db.commit()  # 결과를 알 때까지(settle_unknown) 그대로 둔다
    else:
        sub.failed_attempts += 1
        sub.status = CANCELED if current > end + timedelta(days=GRACE_DAYS) else PAST_DUE
        await db.commit()
    return 1


# ────────────────────────── 청약철회·환불 판단 ──────────────────────────
# 2026-10-22 법률 자문(WO 1-7 · SPEC §3.3·§3.4). 환불은 대표가 토스 상점관리자에서 손으로 하고,
# 여기서는 판단에 쓸 사실(결제 후 첫 대화·체험 사용)을 남기고 관리자 화면에 짐작을 보인다.

# 결제 후 첫 대화·환불을 따지는 결제(첫 결제·갱신·요금제 올리기 — 올리기도 따로 낸 결제다)
PAID_KINDS = ("first", "renew", "upgrade")
KIND_LABELS = {"first": "첫 결제", "renew": "갱신", "upgrade": "요금제 올리기"}
# 첫 결제는 승인 시각에 기간을 연다(_first_month · settle_unknown). 시계 차이 등으로 이만큼 앞선
# 결제도 이번 기간으로 본다
PERIOD_SLACK = timedelta(minutes=1)
# '결제한 날부터 7일 안'(전자상거래법 제17조 제1항)
REFUND_DAYS = 7
REFUND_HINTS = {
    "no_chat_7d": "결제 후 7일 안이고 대화가 없어요 — 전액 환불 대상",
    "no_trial_7d": "체험을 쓰지 않은 대화 상대의 첫 결제 7일 안 — 대화했어도 전액 환불 대상",
    "none": "일반 환불 사유 없음 — 표시·광고와 다르게 드린 경우(받은 날부터 3개월, 안 날부터 "
    "30일)는 따로 판단",
}
# 탈퇴한 계정의 바꾼 주소(api/v1/auth.py DELETED_EMAIL_DOMAIN)
_DELETED_EMAIL = "@deleted.talkto.invalid"


async def trial_used_before(db: AsyncSession, persona_id: str, at: datetime) -> bool | None:
    """이 대화 상대가 at 전에 무료 체험을 실제로 썼나(체험 기간에 만든 분·가족의 대화가 있었나).
    대화 상대가 없으면 None.

    체험은 화면을 열기만 해도 시작돼(entitlement) trial_ends_at 만 보면 써 보지 않은 대화 상대도
    '썼다'가 된다. 약관이 애매하면 고객에게 유리하게 읽으므로(약관법 제5조 제2항) 체험 기간 안에
    대화가 있을 때만 썼다고 본다. 결제 전(프리런치) 무료 대화는 약관의 무료 체험이 아니다
    (체험 기간 밖이라 세지 않는다).
    """
    persona = await db.get(Persona, persona_id)
    if persona is None:
        return None
    end = _aware(persona.trial_ends_at)
    if end is None:
        return False
    # 체험은 열 때부터 trial_days 동안이다(_start_trial)
    start = end - timedelta(days=get_settings().trial_days)
    found = await db.scalar(
        select(UsageEvent.id)
        .where(
            UsageEvent.persona_id == persona_id,
            UsageEvent.created_at >= start,
            UsageEvent.created_at < min(end, at),
        )
        .limit(1)
    )
    return found is not None


async def note_first_chat(*, persona_id: str, user_id: str, member: bool, at: datetime) -> None:
    """대답을 받은 대화 뒤(만든 분·초대한 가족 모두): 이번 이용 기간의 결제 가운데 아직 '결제 후 첫
    대화'가 없는 것에 이 대화를 적는다(청약철회 판단 · WO 1-7).

    갱신하면 새 결제라 다시 적고, 같은 기간의 요금제 올리기 결제도 따로 적는다. 지난 기간 결제는
    기간 시작으로 빠진다. 대화 요청의 세션을 건드리지 않게 따로 연 세션으로 하고, 실패는 기록만
    남긴다(대화는 그대로 끝난다). 결제가 없으면 아무것도 바뀌지 않는다.
    """
    from app.db.session import SessionLocal

    try:
        async with SessionLocal() as own:
            start = await own.scalar(
                select(Subscription.current_period_start).where(
                    Subscription.persona_id == persona_id
                )
            )
            if start is None:
                return
            await own.execute(
                update(Payment)
                .where(
                    Payment.persona_id == persona_id,
                    Payment.status == "DONE",
                    Payment.kind.in_(PAID_KINDS),
                    Payment.approved_at <= at,
                    Payment.approved_at >= _aware(start) - PERIOD_SLACK,
                    Payment.first_chat_at.is_(None),
                )
                .values(first_chat_at=at, first_chat_user_id=user_id, first_chat_member=member)
            )
            await own.commit()
    except Exception:  # noqa: BLE001 — 기록을 못 남겨도 대화는 그대로다
        logger.exception("first chat after payment not recorded")


async def _period_payments(db: AsyncSession, sub: Subscription, at: datetime) -> list[Payment]:
    """이 구독의 이번 기간에 낸 결제(at 까지, 승인 순)."""
    start = _aware(sub.current_period_start)
    return list(
        (
            await db.scalars(
                select(Payment)
                .where(
                    Payment.persona_id == sub.persona_id,
                    Payment.status == "DONE",
                    Payment.kind.in_(PAID_KINDS),
                    Payment.approved_at >= start - PERIOD_SLACK,
                    Payment.approved_at <= at,
                )
                .order_by(Payment.approved_at)
            )
        ).all()
    )


async def prorated_refund(db: AsyncSession, persona_id: str, at: datetime) -> dict | None:
    """회원 잘못이 없이 멈춘(이의 접수 등) 대화 상대의 남은 날 환불 제안(약관 2번 — 일할).

    이번 기간에 낸 결제(첫 결제·갱신과 같은 기간의 올리기)마다 그 결제가 덮는 기간 가운데 남은
    만큼(남은 초 / 덮는 초)을 더해 원 단위로 내린다. 올리기 차액은 올린 때부터 기간 끝까지를
    덮으므로 그 구간으로 나눈다. 이미 돌려준 금액은 뺀다. 구독·남은 날이 없으면 None.
    {"paymentId", "paymentIds", "amount", "remainingDays", "periodDays", "refund"}
    """
    sub = await get_subscription(db, persona_id)
    if sub is None or not sub.current_period_start or not sub.current_period_end:
        return None
    start, end = _aware(sub.current_period_start), _aware(sub.current_period_end)
    at = _aware(at)
    if at >= end:
        return None
    rows = await _period_payments(db, sub, at)
    if not rows:
        return None
    remaining = int((end - max(at, start)).total_seconds())
    total = Fraction(0)
    for pay in rows:
        covers = max(start, _aware(pay.approved_at)) if pay.kind == "upgrade" else start
        span = max(1, int((end - covers).total_seconds()))
        share = Fraction(pay.amount * min(remaining, span), span) - (pay.refund_amount or 0)
        total += max(Fraction(0), share)
    base = next((p for p in reversed(rows) if p.kind != "upgrade"), rows[-1])
    return {
        "paymentId": base.id,
        "paymentIds": [p.id for p in rows],
        "amount": sum(p.amount for p in rows),
        "remainingDays": math.ceil(remaining / 86400),
        "periodDays": round((end - start).total_seconds() / 86400),
        "refund": math.floor(total),
    }


def refund_deadline(approved_at: datetime) -> datetime:
    """'결제한 날부터 7일 안'이 끝나는 때: 결제한 날(한국 날짜)은 세지 않고 7일째 되는 날이 끝날 때
    (민법 제157조). 예: 10월 1일 오후 결제 → 10월 8일 24시(한국)."""
    k = _aware(approved_at).astimezone(KST)
    day = datetime(k.year, k.month, k.day, tzinfo=KST)
    return (day + timedelta(days=REFUND_DAYS + 1)).astimezone(UTC)


def refund_hint(pay: Payment, *, first_paid_id: str | None, at: datetime) -> dict[str, Any]:
    """관리자 환불 화면의 짐작(SPEC §3.4). 결정은 대표가 한다.

    1 no_chat_7d: 결제한 날부터 7일 안이고 결제 후 대화가 없다(가족의 대화도 센다)
    2 no_trial_7d: 그 대화 상대의 첫 결제이고 체험을 쓰지 않았고 7일 안(대화했어도)
    3 none: 그 밖(표시·광고와 다르게 드린 경우는 따로 판단)
    """
    if pay.status == "DONE" and pay.kind in PAID_KINDS and pay.approved_at is not None:
        until = refund_deadline(pay.approved_at)
        if _aware(at) <= until:
            if pay.first_chat_at is None:
                return {"code": "no_chat_7d", "text": REFUND_HINTS["no_chat_7d"], "until": until}
            if pay.id == first_paid_id and pay.trial_used is False:
                return {"code": "no_trial_7d", "text": REFUND_HINTS["no_trial_7d"], "until": until}
    return {"code": "none", "text": REFUND_HINTS["none"], "until": None}


async def first_paid(db: AsyncSession, persona_ids: set[str]) -> dict[str, Payment]:
    """대화 상대마다 처음 결제한 첫 결제(kind=first, DONE)."""
    if not persona_ids:
        return {}
    rows = (
        await db.scalars(
            select(Payment)
            .where(
                Payment.persona_id.in_(persona_ids),
                Payment.status == "DONE",
                Payment.kind == "first",
                Payment.approved_at.is_not(None),
            )
            .order_by(Payment.approved_at)
        )
    ).all()
    found: dict[str, Payment] = {}
    for pay in rows:
        found.setdefault(pay.persona_id, pay)
    return found


def _period_of(
    pay: Payment, sub: Subscription | None, history: list[Payment]
) -> tuple[datetime | None, datetime | None]:
    """이 결제가 낸 이용 기간. 이번 기간이면 구독 값, 지난 기간은 결제 시각으로 짐작한다(올리기는
    그 앞의 첫 결제·갱신 기간)."""
    approved = _aware(pay.approved_at)
    if approved is None or pay.status != "DONE":
        return None, None
    if sub is not None and sub.current_period_start and sub.current_period_end:
        start, end = _aware(sub.current_period_start), _aware(sub.current_period_end)
        if start - PERIOD_SLACK <= approved < end:
            return start, end
    base = pay
    if pay.kind == "upgrade":
        earlier = [h for h in history if h.kind != "upgrade" and _aware(h.approved_at) <= approved]
        if not earlier:
            return None, None
        base = earlier[-1]
    start = _aware(base.approved_at)
    day = sub.billing_day if sub is not None and sub.billing_day else kst_day(start)
    return start, add_month(start, day)


def _owner_masked(owner: User | None) -> str:
    from app.services import notify

    if owner is None or (owner.email or "").endswith(_DELETED_EMAIL):
        return "탈퇴한 회원"
    return notify.masked_email(owner.email)


async def admin_rows(
    db: AsyncSession, payments: list[Payment], *, at: datetime | None = None
) -> list[dict[str, Any]]:
    """관리자 환불 화면의 결제 줄(SPEC §3.4). 성함·전체 이메일은 싣지 않는다(호칭, 가린 이메일).

    대화 상대가 남아 있으면 체험 끝(trialEndsAt)도, 첫 결제 줄에는 결제 전 대화 수도 보인다.
    """
    from app.services import notices

    current = at or now()
    pids = {p.persona_id for p in payments if p.persona_id}
    personas: dict[str, Persona] = {}
    subs: dict[str, Subscription] = {}
    history: dict[str, list[Payment]] = {}
    if pids:
        found = await db.scalars(select(Persona).where(Persona.id.in_(pids)))
        personas = {p.id: p for p in found.all()}
        found = await db.scalars(select(Subscription).where(Subscription.persona_id.in_(pids)))
        subs = {s.persona_id: s for s in found.all()}
        done = await db.scalars(
            select(Payment)
            .where(
                Payment.persona_id.in_(pids),
                Payment.status == "DONE",
                Payment.approved_at.is_not(None),
            )
            .order_by(Payment.approved_at)
        )
        for pay in done.all():
            history.setdefault(pay.persona_id, []).append(pay)
    firsts = await first_paid(db, pids)
    user_ids = {p.user_id for p in payments}
    users: dict[str, User] = {}
    if user_ids:
        found = await db.scalars(select(User).where(User.id.in_(user_ids)))
        users = {u.id: u for u in found.all()}
    out = []
    for pay in payments:
        pid = pay.persona_id
        persona = personas.get(pid) if pid else None
        sub = subs.get(pid) if pid else None
        first = firsts.get(pid) if pid else None
        start, end = _period_of(pay, sub, history.get(pid, []) if pid else [])
        chats_before = None
        if pid and pay.kind == "first":
            # 결제 전 대화 수(체험·결제 전 무료 대화 모두 — 대화 횟수 기록은 지워도 남는다)
            chats_before = int(
                await db.scalar(
                    select(func.count(UsageEvent.id)).where(
                        UsageEvent.persona_id == pid,
                        UsageEvent.created_at < (pay.approved_at or pay.created_at),
                    )
                )
                or 0
            )
        if pay.first_chat_at is None:
            chat_by = None
        else:
            chat_by = "초대한 가족" if pay.first_chat_member else "만든 분"
        if pid is None:
            label = None
        else:
            label = notices.persona_label(persona) if persona else "지운 대화 상대"
        out.append(
            {
                "id": pay.id,
                "orderId": pay.order_id,
                "userId": pay.user_id,
                "approvedAt": _aware(pay.approved_at),
                "createdAt": _aware(pay.created_at),
                "kind": pay.kind,
                "kindLabel": KIND_LABELS.get(pay.kind or "", pay.kind),
                "plan": pay.plan,
                "planName": plan_name(pay.plan),
                "amount": pay.amount,
                "status": pay.status,
                "personaId": pid,
                "personaLabel": label,
                "ownerMasked": _owner_masked(users.get(pay.user_id)),
                "firstChatAt": _aware(pay.first_chat_at),
                "firstChatBy": chat_by,
                # 첫 결제 줄은 그때 남긴 값, 다른 줄은 그 대화 상대의 첫 결제 값
                "trialUsed": pay.trial_used
                if pay.kind == "first"
                else (first.trial_used if first else None),
                "trialEndsAt": _aware(persona.trial_ends_at) if persona else None,
                "chatsBeforePayment": chats_before,
                "personaFirstPaidAt": _aware(first.approved_at) if first else None,
                "periodStart": start,
                "periodEnd": end,
                "refundHint": refund_hint(
                    pay, first_paid_id=first.id if first else None, at=current
                ),
                "contractSentAt": _aware(pay.contract_sent_at),
                "refundedAt": _aware(pay.refunded_at),
                "refundAmount": pay.refund_amount,
                "refundNote": pay.refund_note,
            }
        )
    return out
