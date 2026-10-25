"""요금제·구독·결제 API. 결제는 페르소나마다 받는다(구독·체험·대화 횟수 모두 페르소나 단위).

결제 창은 이 서버가 그린다(토스페이먼츠 결제창 스크립트를 띄우는 작은 페이지). 웹은 그 주소로
창 전체를 옮기고, 카드 등록이 끝나면 이 서버가 첫 달을 결제한 뒤 웹 #/plans?persona= 로 돌려보낸다.
소셜 로그인과 같은 방식이라 웹(Flutter)에 결제 회사 스크립트를 넣지 않아도 된다.
이미 다른 페르소나에 카드를 등록했으면 결제 창 없이 그 카드로 바로 시작한다.
"""

import secrets
from datetime import timedelta
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser
from app.api.v1.auth import _safe_return_to
from app.api.v1.persona_setup import owned_persona
from app.core.config import get_settings
from app.db.models import (
    CheckoutSession,
    LifeStatus,
    Payment,
    Person,
    Persona,
    SetupStep,
    SubscriptionRequest,
    User,
    UserRole,
)
from app.schemas.common import APIModel, Envelope
from app.services import analytics, billing, legal, sharing

router = APIRouter(prefix="/billing", tags=["Billing"])

CHECKOUT_TTL = timedelta(minutes=30)


class CheckoutRequest(APIModel):
    persona_id: str = Field(max_length=36)
    plan: Literal["memories", "family"]
    return_to: str | None = Field(default=None, max_length=2000)
    # 화면이 알려 준 결제 방법과 값. 그사이 달라졌으면 결제하지 않고 다시 보여 주게 한다.
    #   change(이 페르소나 구독 바꾸기) · saved_card(등록된 카드로 시작) · window(결제 창)
    mode: Literal["change", "saved_card", "window"] | None = None
    expected_amount: int | None = Field(default=None, ge=0)
    # 결제 직전 확인 두 가지(정기결제·결제 취소 제한). 매번 받는다.
    agreements: list[str] = Field(default_factory=list, max_length=10)


# 결제 전에 꼭 받는 확인(payment 문서). 화면은 /legal 의 payment 로 같은 두 글을 받아 보인다
PAYMENT_AGREEMENTS = tuple(legal.PAYMENT_LABELS)


class PersonaRequest(APIModel):
    persona_id: str = Field(max_length=36)


class SubscriptionRequestCreate(APIModel):
    """결제 전 구독 신청(돈은 나가지 않는다)."""

    persona_id: str = Field(max_length=36)
    plan: Literal["memories", "family"]


# 결제 전 모드에서 바로 결제하려 할 때
PRELAUNCH_CHECKOUT = "지금은 결제가 열리기 전이라 구독 신청만 받아요."
# 결제가 열린 뒤에 구독 신청을 하려 할 때
PAYMENTS_OPENED = "이제 바로 결제할 수 있어요. 요금제 화면에서 시작해 주세요."


def persona_title(persona: Persona, person: Person | None) -> str:
    """화면에서 부르는 말(호칭 → 관계 → '그분'). 성함은 내보내지 않는다."""
    basics = persona.basics or {}
    for value in (
        basics.get("addressTerm"),
        person.address_term if person else None,
        basics.get("relation"),
        person.relationship_label if person else None,
    ):
        if value and str(value).strip():
            return str(value).strip()
    return "그분"


def persona_relation(persona: Persona, person: Person | None) -> str | None:
    """호칭과 다를 때만 관계(예: 호칭 '할머니' · 관계 '외할머니').

    같은 호칭이 둘이면 이것으로 가른다.
    """
    basics = persona.basics or {}
    relation = basics.get("relation") or (person.relationship_label if person else None)
    relation = str(relation).strip() if relation else None
    return relation if relation and relation != persona_title(persona, person) else None


def _subscription_view(sub) -> dict | None:
    if sub is None:
        return None
    return {
        "plan": sub.plan,
        "status": sub.status,
        "periodEnd": billing._aware(sub.current_period_end),
        "cancelAtPeriodEnd": sub.cancel_at_period_end,
        "nextPlan": sub.next_plan,
        "cardLabel": sub.card_label,
        "provider": sub.provider,
    }


async def _persona_view(
    db: DB,
    user: User,
    persona: Persona,
    person: Person | None,
    trial_will_start: bool | None = None,
    ent: billing.Entitlement | None = None,
) -> dict:
    if ent is None:
        ent = await billing.entitlement(db, user, persona)
    sub = await billing.get_subscription(db, persona.id)
    beta_available = await billing.beta_open(db)
    prov = billing.provider()
    upgrades = {}
    if sub is not None and ent.status in ("ACTIVE", "CANCELING"):
        for plan in billing.plans().values():
            if plan.id in billing.PAID_PLANS:
                amount = billing.upgrade_amount(sub, user, plan, beta_available)
                if amount is not None:
                    upgrades[plan.id] = amount
    return {
        **billing.view(ent),
        "title": persona_title(persona, person),
        # 호칭이 같은 대화 상대가 둘일 때 어느 분인지 가르는 관계(호칭과 같으면 None)
        "relation": persona_relation(persona, person),
        "step": persona.setup_step,
        # 결제한 적이 있는 구독인가(첫 결제가 실패해 남은 빈 줄은 아니다)
        "hadPaidPlan": bool(sub and sub.current_period_start),
        # 결과를 모르는 결제가 있다(정해질 때까지 요금제를 바꾸지 않는다)
        "paymentPending": await billing.pending_for_persona(db, persona.id),
        # 지금 고르면 바로 결제할 올리기 차액
        "upgradeQuotes": upgrades,
        # 등록된 카드로 바로 바꾸는가(checkout 의 changing 과 같은 규칙 — 화면이 이것을 그대로 쓴다)
        "canChange": bool(
            sub
            and sub.billing_key
            and sub.status == billing.ACTIVE
            and prov is not None
            and sub.provider == prov.name
        ),
        # 만드는 중이면, 다 만들었을 때 체험이 시작되나(계정의 체험 자리가 남았나)
        "trialWillStart": trial_will_start,
        # 돌아가신 분인가(모르면 돌아가신 분으로 본다). 예전 화면이 읽어서 둔다
        "deceased": person is None or person.life_status != LifeStatus.LIVING,
        "subscription": _subscription_view(sub),
        # 결제 전 구독 신청(신청 중인 것만). 결제가 열린 뒤에도 보여 준다.
        "request": billing.request_view(await billing.get_request(db, persona.id)),
    }


@router.get("/plans", response_model=Envelope[list[dict]])
async def list_plans(db: DB):
    return Envelope(data=billing.plans_view(None, await billing.beta_open(db)))


@router.get("/me", response_model=Envelope[dict])
async def my_billing(user: CurrentUser, db: DB):
    """가족(계정) 정보와 페르소나마다의 요금제 상태."""
    settings = get_settings()
    rows = (
        await db.execute(
            select(Persona, Person)
            .join(Person, Person.id == Persona.person_id)
            .where(Person.owner_user_id == user.id)
            .order_by(Persona.created_at)
        )
    ).all()
    # 먼저 모두 본다(다 만든 페르소나는 이때 체험이 시작돼 남은 자리가 줄어든다)
    personas = [await _persona_view(db, user, persona, person) for persona, person in rows]
    # 그다음 만드는 중인 페르소나에 먼저 만든 것부터 남은 체험 자리를 나눈다
    slots = billing.trials_left(user)
    for view, (persona, _) in zip(personas, rows, strict=True):
        if persona.trial_ends_at is None and persona.setup_step != SetupStep.READY:
            if not billing.payments_open(user):
                # 결제 전에는 체험 자리를 쓰지 않고, 다 만들면 무료로 쓴다
                view["trialWillStart"] = True
                continue
            view["trialWillStart"], slots = slots > 0, slots - 1
    prov = billing.provider()
    card = await billing.card_on_file(db, user)
    return Envelope(
        data={
            "personas": personas,
            "admin": user.role == UserRole.ADMIN,
            "provider": prov.name if prov else None,
            "testMode": bool(prov and prov.test_mode),
            "plans": billing.plans_view(user, await billing.beta_open(db)),
            "cardLabel": card.card_label if card else None,
            "betaUntil": billing._aware(user.beta_until),
            # 초기 가족 요금이 30일 안에 끝나는데 정식 요금 동의를 아직 받지 않았다
            "regularPriceConsentNeeded": billing.beta_ends_soon(user)
            and user.regular_price_consent_at is None,
            "regularPriceConsentedAt": billing._aware(user.regular_price_consent_at),
            "betaMonths": settings.beta_months,
            "betaFamilies": settings.beta_families,
            "trialDays": settings.trial_days,
            "trialConversations": settings.trial_conversations,
            "trialPersonasPerAccount": settings.trial_personas_per_account,
            "trialsLeft": billing.trials_left(user),
            # 지금 새로 만들면 체험을 받을 수 있나(만드는 중인 페르소나가 먼저 받는다)
            "trialsAvailableForNew": max(0, slots),
            # 결제가 열렸나(false = 결제 전 모드: 구독 신청만 받고 무료 체험에 한도가 없다)
            "paymentsOpen": billing.payments_open(user),
            # 결제 전 모드의 대화 상대마다 한 달 대화 상한(null = 제한 없음)
            "prelaunchConversations": settings.prelaunch_conversations,
        }
    )


@router.get("/personas/{persona_id}", response_model=Envelope[dict])
async def persona_billing(persona_id: str, user: CurrentUser, db: DB):
    """페르소나 하나의 요금제 상태(대화 화면의 남은 대화 횟수 등).

    주인에게는 함께 쓰는 가족 수와 지금 요금제로 함께 쓸 수 있는지를 더한다. 함께 쓰는 가족에게는
    주인의 권리로 만든 짧은 모양만 보인다(요금제·카드·결제·구독 내용 없이).
    """
    access = await sharing.chat_access(db, persona_id, user)
    if access.member:
        return Envelope(data=await _member_view(db, access))
    persona, person = access.persona, access.person
    ent = await billing.entitlement(db, user, persona)
    view = await _persona_view(db, user, persona, person, ent=ent)
    view["members"] = await sharing.joined_count(db, persona.id)
    view["sharingAllowed"] = sharing.sharing_allowed(ent)
    return Envelope(data=view)


# 가족에게 보일 상태: 관리자·해지 예정·결제 확인 중도 '쓸 수 있음(ACTIVE)'으로만 보인다
MEMBER_STATUSES = {"PRELAUNCH": "PRELAUNCH", "TRIAL": "TRIAL"}


async def _member_view(db: DB, access: sharing.ChatAccess) -> dict:
    """함께 쓰는 가족의 요금제 보기. 남은 횟수는 가족이 함께 쓰는 것이다."""
    persona, person = access.persona, access.person
    ent, reason = await sharing.member_entitlement(db, access)
    owner = sharing.owner_label(access)
    counts = billing.view(ent) if ent is not None else {}
    return {
        "personaId": persona.id,
        "role": "member",
        "status": "PAUSED" if reason else MEMBER_STATUSES.get(ent.status, "ACTIVE"),
        "active": reason is None,
        "conversationsLimit": counts.get("conversationsLimit"),
        "conversationsUsed": counts.get("conversationsUsed", 0),
        "conversationsLeft": counts.get("conversationsLeft"),
        "conversationsResetAt": counts.get("conversationsResetAt"),
        "madeAt": counts.get("madeAt"),
        "textOnly": reason is None and bool(counts.get("textOnly")),
        "step": persona.setup_step,
        "title": (access.share.profile or {}).get("addressTerm"),
        "relation": None,
        "deceased": person is None or person.life_status != LifeStatus.LIVING,
        "ownerName": owner,
        "paymentsOpen": billing.payments_open(access.owner),
        "paused": {"reason": reason, "text": sharing.paused_text(reason, owner, ent)}
        if reason
        else None,
    }


@router.post("/requests", response_model=Envelope[dict])
async def request_subscription(payload: SubscriptionRequestCreate, user: CurrentUser, db: DB):
    """결제가 열리기 전: 이 대화 상대의 요금제를 구독 신청한다(돈은 나가지 않는다).

    이미 신청했으면 요금제를 바꾸고 다시 '신청함'으로 둔다. 응답 = 그 대화 상대의 요금제 보기.
    """
    if billing.payments_open(user):
        raise HTTPException(status.HTTP_409_CONFLICT, PAYMENTS_OPENED)
    persona = await owned_persona(db, payload.persona_id, user.id)
    person = await db.get(Person, persona.person_id)
    req = await billing.get_request(db, persona.id)
    changed = False
    if req is None:
        req = SubscriptionRequest(user_id=user.id, persona_id=persona.id, plan=payload.plan)
        db.add(req)
        try:
            await db.commit()
            changed = True
        except IntegrityError:
            # 같은 대화 상대를 동시에 두 번 신청했다. 먼저 생긴 줄을 고친다.
            await db.rollback()
            await db.refresh(user)
            persona = await owned_persona(db, payload.persona_id, user.id)
            person = await db.get(Person, persona.person_id)
            req = await billing.get_request(db, persona.id)
    if req.plan != payload.plan or req.status != billing.REQUESTED:
        req.plan, req.status, req.user_id = payload.plan, billing.REQUESTED, user.id
        req.created_at = billing.now()
        changed = True
    await db.commit()
    if changed:
        await analytics.track(
            db,
            "subscription_requested",
            user_id=user.id,
            persona_id=persona.id,
            plan=payload.plan,
            notify_email=user.email,
        )
    return Envelope(data=await _persona_view(db, user, persona, person))


@router.delete("/requests/{persona_id}", response_model=Envelope[dict])
async def cancel_subscription_request(persona_id: str, user: CurrentUser, db: DB):
    """구독 신청 취소. 신청이 없어도 그대로 돌려준다. 응답 = 그 대화 상대의 요금제 보기."""
    persona = await owned_persona(db, persona_id, user.id)
    person = await db.get(Person, persona.person_id)
    req = await billing.get_request(db, persona.id)
    if req is not None and req.status == billing.REQUESTED:
        req.status = "CANCELED"
        await db.commit()
        await analytics.track(
            db,
            "subscription_request_canceled",
            user_id=user.id,
            persona_id=persona.id,
            plan=req.plan,
        )
    return Envelope(data=await _persona_view(db, user, persona, person))


async def _checkout_conflict(text: str = "요금이나 결제 방법이 바뀌었어요. 다시 확인해 주세요."):
    raise HTTPException(status.HTTP_409_CONFLICT, text)


@router.post("/checkout", response_model=Envelope[dict])
async def start_checkout(payload: CheckoutRequest, user: CurrentUser, db: DB):
    """이 페르소나의 요금제 고르기.

    이미 이 페르소나 구독이 있으면 바로 바꾸고, 다른 페르소나에 등록한 카드가 있으면 그 카드로 바로
    시작한다. 둘 다 아니면 결제 창 주소를 준다. 결제 전 모드면(관리자도) 409 — 구독 신청만 받는다.
    """
    if not billing.payments_open(user):
        raise HTTPException(status.HTTP_409_CONFLICT, PRELAUNCH_CHECKOUT)
    prov = billing.provider()
    if prov is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, billing.NOT_OPEN)
    persona = await owned_persona(db, payload.persona_id, user.id)
    if persona.setup_step != SetupStep.READY:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "대화 상대를 다 만든 뒤에 요금제를 고를 수 있어요."
        )
    sub = await billing.get_subscription(db, persona.id)
    changing = bool(
        sub and sub.billing_key and sub.status == billing.ACTIVE and sub.provider == prov.name
    )
    # 화면이 안내한 방법과 지금 할 일이 다르면 결제하지 않고 다시 보여 주게 한다
    # (예: '결제 창에서 카드를 등록해요'라고 했는데 등록된 카드로 바로 결제하지 않게)
    if payload.mode == "change" and not changing:
        await _checkout_conflict()
    if payload.mode in ("saved_card", "window") and changing:
        await _checkout_conflict()
    # 지금 요금제를 다시 골라 예약한 변경을 취소하는 것은 결제가 아니라 확인을 받지 않는다
    keeping = changing and sub.plan == payload.plan
    if not keeping:
        if not all(item in payload.agreements for item in PAYMENT_AGREEMENTS):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "결제 전 확인 두 가지에 체크해 주세요."
            )
        for item in PAYMENT_AGREEMENTS:
            legal.log(
                db, user.id, legal.persona_type(item, persona.id), True, persona_id=persona.id
            )
        await db.commit()
        await analytics.track(
            db, "checkout_started", user_id=user.id, persona_id=persona.id, plan=payload.plan
        )
    try:
        # 쓰고 있는 구독(ACTIVE)만 등록된 카드로 바로 바꾼다. 결제가 밀린 구독은 카드를 다시 받는다.
        if changing:
            result = await billing.change_plan(
                db, user, persona, payload.plan, payload.expected_amount
            )
        elif payload.mode == "window":
            result = {"needsCheckout": True}
        else:
            result = await billing.start_with_card(
                db, user, persona, payload.plan, payload.expected_amount
            )
    except billing.PriceChanged as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except billing.BillingError as error:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(error)) from error
    if not result.get("needsCheckout"):
        return Envelope(data=result)
    return_to = _safe_return_to(payload.return_to or get_settings().frontend_url)
    if return_to is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "이 화면에서는 결제할 수 없어요. TalkTo 요금제 화면에서 다시 해 주세요.",
        )
    session = CheckoutSession(
        id=secrets.token_urlsafe(32),
        user_id=user.id,
        persona_id=persona.id,
        plan=payload.plan,
        customer_key=await billing.ensure_customer_key(db, user),
        return_to=return_to,
        expires_at=billing.now() + CHECKOUT_TTL,
    )
    db.add(session)
    await db.commit()
    settings = get_settings()
    url = f"{settings.public_base_url.rstrip('/')}{settings.api_v1_prefix}/billing/checkout-page"
    return Envelope(data={"checkoutUrl": f"{url}?session={session.id}"})


EXPIRED_TEXT = "결제 시간이 지났어요. 요금제 화면에서 다시 시도해 주세요."


async def _open_session(db: DB, session_id: str) -> CheckoutSession:
    session = await db.get(CheckoutSession, session_id)
    if (
        session is None
        or session.status != "OPEN"
        or billing._aware(session.expires_at) < billing.now()
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, EXPIRED_TEXT)
    return session


async def _take_session(db: DB, session_id: str, to: str) -> CheckoutSession:
    """결제 창 표를 한 번에 닫으며 가져간다. 두 번 돌아와도(새로고침·동시 요청) 한 번만 처리된다."""
    result = await db.execute(
        update(CheckoutSession)
        .where(
            CheckoutSession.id == session_id,
            CheckoutSession.status == "OPEN",
            CheckoutSession.expires_at > billing.now(),
        )
        .values(status=to)
    )
    await db.commit()
    if result.rowcount != 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, EXPIRED_TEXT)
    return await db.get(CheckoutSession, session_id, populate_existing=True)


def _back(
    return_to: str,
    persona_id: str | None,
    *,
    result: str | None = None,
    error: str | None = None,
):
    query = f"result={result}" if result else f"error={quote(error or '', safe='')}"
    if persona_id:
        query = f"persona={quote(persona_id, safe='')}&{query}"
    return RedirectResponse(f"{return_to}#/plans?{query}", 302)


async def _session_persona(db: DB, checkout: CheckoutSession) -> Persona | None:
    """결제 창을 연 페르소나(그사이 지웠거나 남의 것이면 None)."""
    persona = await db.get(Persona, checkout.persona_id) if checkout.persona_id else None
    if persona is None:
        return None
    person = await db.get(Person, persona.person_id)
    return persona if person and person.owner_user_id == checkout.user_id else None


@router.get("/checkout-page", response_class=HTMLResponse, include_in_schema=False)
async def checkout_page(db: DB, session: str = Query(max_length=64)):
    checkout = await _open_session(db, session)
    user = await db.get(User, checkout.user_id)
    prov = billing.provider()
    if prov is None:
        return _back(checkout.return_to, checkout.persona_id, error=billing.NOT_OPEN)
    settings = get_settings()
    base = f"{settings.public_base_url.rstrip('/')}{settings.api_v1_prefix}/billing/callback"
    plan = billing.plans().get(checkout.plan)
    if plan is None:  # 없앤 요금제로 연 창(시작할 때 닫지만, 그 사이에 열렸을 수 있다)
        return _back(checkout.return_to, checkout.persona_id, error=EXPIRED_TEXT)
    if checkout.quoted_amount is None:
        # 처음 보여 줄 때 값을 정해 둔다. 카드를 받고 돌아오면 이 값 그대로 결제한다.
        amount, beta = await billing.quote(db, user, plan)
        checkout.quoted_amount, checkout.quoted_beta = amount, beta
        await db.commit()
    amount, beta = checkout.quoted_amount, bool(checkout.quoted_beta)
    html = prov.checkout_html(
        customer_key=checkout.customer_key,
        plan=plan,
        amount=amount,
        beta=beta,
        success_url=f"{base}/success?session={checkout.id}",
        fail_url=f"{base}/fail?session={checkout.id}",
        customer_name=user.name,
        customer_email=None if user.email.endswith(".invalid") else user.email,
        beta_until=user.beta_until,
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/callback/success", include_in_schema=False)
async def checkout_success(
    db: DB,
    session: str = Query(max_length=64),
    authKey: str = Query(max_length=300),  # noqa: N803 — 결제 회사가 붙이는 이름 그대로
    customerKey: str = Query(max_length=300),  # noqa: N803
):
    # 한 번만 쓴다. 새로고침이나 뒤로 가기로 다시 와도 두 번 결제되지 않게 먼저 닫는다.
    checkout = await _take_session(db, session, "DONE")
    if customerKey != checkout.customer_key:
        return _back(
            checkout.return_to,
            checkout.persona_id,
            error="결제를 확인하지 못했어요. 요금제 화면에서 다시 해 주세요.",
        )
    user = await db.get(User, checkout.user_id)
    persona = await _session_persona(db, checkout)
    if persona is None:
        checkout.status = "FAILED"
        await db.commit()
        return _back(checkout.return_to, None, error="대화 상대를 찾지 못해 결제하지 않았어요.")
    try:
        await billing.activate(
            db,
            user,
            persona,
            checkout.plan,
            auth_key=authKey,
            customer_key=checkout.customer_key,
            quoted=(
                (checkout.quoted_amount, bool(checkout.quoted_beta))
                if checkout.quoted_amount is not None
                else None
            ),
        )
    except billing.BillingError as error:
        checkout.status = "FAILED"
        await db.commit()
        return _back(checkout.return_to, persona.id, error=str(error))
    # 이 페르소나로 열어 둔 다른 결제 창은 닫는다(나중에 끝내도 한 달 값을 또 받지 않게)
    await db.execute(
        update(CheckoutSession)
        .where(
            CheckoutSession.user_id == user.id,
            CheckoutSession.persona_id == persona.id,
            CheckoutSession.status == "OPEN",
        )
        .values(status="EXPIRED")
    )
    await db.commit()
    return _back(checkout.return_to, persona.id, result="success")


@router.get("/callback/fail", include_in_schema=False)
async def checkout_fail(
    db: DB,
    session: str = Query(max_length=64),
    message: str | None = Query(None, max_length=500),
    code: str | None = Query(None, max_length=100),
):
    checkout = await _take_session(db, session, "FAILED")
    cancelled = code in ("USER_CANCEL", "PAY_PROCESS_CANCELED")
    text = "결제를 취소했어요." if cancelled else (message or "결제하지 못했어요.")
    return _back(checkout.return_to, checkout.persona_id, error=text)


@router.post("/cancel", response_model=Envelope[dict])
async def cancel_subscription(payload: PersonaRequest, user: CurrentUser, db: DB):
    """이 페르소나 구독 해지: 이번 기간이 끝날 때까지는 그대로 쓰고, 다음 결제부터 멈춘다."""
    persona = await owned_persona(db, payload.persona_id, user.id)
    try:
        await billing.cancel(db, persona)
    except billing.BillingError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return Envelope(data={"ok": True})


@router.post("/resume", response_model=Envelope[dict])
async def resume_subscription(payload: PersonaRequest, user: CurrentUser, db: DB):
    persona = await owned_persona(db, payload.persona_id, user.id)
    try:
        await billing.resume(db, persona)
    except billing.BillingError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return Envelope(data={"ok": True})


@router.post("/regular-price-consent", response_model=Envelope[dict])
async def consent_regular_price(user: CurrentUser, db: DB):
    """초기 가족 요금이 끝난 뒤 정식 요금으로 이어 가겠다는 동의. 없으면 그때 구독이 끝난다."""
    at = billing.now()
    user.regular_price_consent_at = at
    # 지금 상태(consents)와 이력(consent_records) 둘 다 남긴다
    await legal.record(db, user, "regular_price", True, doc="payment")
    await db.commit()
    return Envelope(data={"ok": True, "consentedAt": at})


@router.get("/payments", response_model=Envelope[list[dict]])
async def payment_history(user: CurrentUser, db: DB):
    rows = (
        await db.scalars(
            select(Payment)
            .where(Payment.user_id == user.id)
            .order_by(Payment.created_at.desc())
            .limit(50)
        )
    ).all()
    titles: dict[str, str] = {}
    relations: dict[str, str | None] = {}
    for pid in {p.persona_id for p in rows if p.persona_id}:
        persona = await db.get(Persona, pid)
        if persona is not None:
            person = await db.get(Person, persona.person_id)
            titles[pid] = persona_title(persona, person)
            relations[pid] = persona_relation(persona, person)
    return Envelope(
        data=[
            {
                "id": p.id,
                "personaId": p.persona_id,
                # 지운 페르소나는 '지운 대화 상대'(페르소나별 결제 전의 기록은 비워 둔다)
                "personaTitle": titles.get(p.persona_id, "지운 대화 상대")
                if p.persona_id
                else None,
                "personaRelation": relations.get(p.persona_id) if p.persona_id else None,
                "plan": p.plan,
                "planName": billing.plan_name(p.plan),
                "amount": p.amount,
                "status": p.status,
                "failure": p.failure,
                "receiptUrl": p.receipt_url,
                "at": billing._aware(p.approved_at or p.created_at),
                "provider": p.provider,
                # 돌려드린 결제(관리자가 손으로 환불한 뒤 적은 것)
                "refundedAt": billing._aware(p.refunded_at),
                "refundAmount": p.refund_amount,
            }
            for p in rows
        ]
    )
