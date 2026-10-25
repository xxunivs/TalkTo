"""결제 뒤 계약 내용 교부(전자상거래법 제13조 제2항 · WO 1-8 · SPEC §3.5).

구독 기간을 정한 뒤(첫 결제·요금제 올리기·갱신·결과를 모르던 결제가 정해진 뒤 — billing 이 부른다)
요금제·금액·이용 기간·다음 결제일·청약철회와 환불 방법을 앱 알림(kind=contract)으로 남기고, 고객
메일이 켜져 있으면(mailer.user_mail_configured) 같은 글을 메일로도 보낸다. 메일이 안 되는 분(카카오
가입자 등)도 앱 알림으로 남는다. 계약 내용 알림은 읽어도 하루 정리에서 지우지 않고 탈퇴 때 지운다.

결제는 아직 닫혀 있다(PAYMENTS_OPEN=false). 코드는 그대로 두고 결제 시험 계정·시험 키에서만 돈다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.db.models import Payment, Persona, Subscription, User
from app.services import billing, legal, notices

logger = logging.getLogger(__name__)

TITLE = "결제 내용과 청약철회 안내 — {label}"


def _when(value: datetime | None) -> datetime:
    return (billing._aware(value) or datetime.now(UTC)).astimezone(billing.KST)


def _day(value: datetime) -> str:
    return f"{value.year}년 {value.month}월 {value.day}일"


def _won(value: int) -> str:
    return f"{value:,}원"


def render(
    pay: Payment,
    sub: Subscription,
    plan: billing.Plan | None,
    user: User,
    persona_label: str,
) -> tuple[str, str]:
    """(제목, 본문) — 순수 함수(SPEC §3.5 최종 문구). 값은 결제·구독·설정(legal)에서만 온다.

    이용 기간은 결제일부터 다음 결제일의 전날까지(약관 4번 · 결제 안내), 다음 결제일은 기간 끝이다.
    해지 예정인 구독(결과를 기다리는 사이 해지한 경우)은 다음 결제가 없다고 알린다.
    """
    values = legal.placeholders()
    biz = legal.business()

    def shown(value: str | None) -> str:
        return value or legal.PENDING

    plan_title = plan.name if plan is not None else billing.plan_name(pay.plan)
    monthly = billing.price_for(plan, user) if plan is not None else pay.amount
    paid = _when(pay.approved_at)
    start = _when(sub.current_period_start)
    end = _when(sub.current_period_end)
    last = end - timedelta(days=1)
    kind = billing.KIND_LABELS.get(pay.kind or "", "결제")
    if sub.cancel_at_period_end:
        next_line = (
            f"- 다음 결제일: 없어요. 해지하셔서 {_day(last)}까지 쓰시고 그 뒤로는 결제되지 않아요."
        )
    else:
        next_line = (
            f"- 다음 결제일: {_day(end)}. 매달 같은 날 자동으로 결제돼요. 요금제 화면에서 언제든 "
            "해지할 수 있고, 해지해도 이용 기간이 끝나는 날까지 쓸 수 있어요."
        )
    email, phone = values["문의 이메일"], values["전화"]
    body = "\n".join(
        [
            f"{user.name or '회원'}님, TalkTo 결제가 끝났어요. 아래 계약 내용을 보관해 주세요.",
            "",
            f"- 대화 상대: {persona_label}",
            f"- 요금제: {plan_title} 요금제(월 {_won(monthly)}, 부가세 포함)",
            f"- 이번 결제: {_won(pay.amount)} · {_day(paid)} {paid:%H:%M} · 주문 번호 "
            f"{pay.order_id} · {kind}",
            f"- 이용 기간: {_day(start)}부터 {_day(last)}까지",
            next_line,
            "",
            "결제 취소(청약철회)와 환불",
            "- 결제하시면 바로 유료 이용이 시작돼요. 결제한 뒤 이 대화 상대와 대화를 시작하면"
            "(초대한 가족이 시작해도 같아요) 단순 변심에 의한 결제 취소(청약철회)가 제한돼요"
            "(전자상거래법 제17조 제2항 제5호).",
            "- 다만 아래의 경우에는 전액 돌려드려요.",
            "  1. 결제한 날부터 7일 안에 이 대화 상대와 한 번도 대화하지 않으셨을 때",
            "  2. 무료 체험을 쓰지 않은 대화 상대를 처음 결제한 날부터 7일 안에 말씀하실 때"
            "(대화하셨더라도)",
            "  3. 표시·광고나 안내한 내용, 계약 내용과 다르게 드렸을 때(받으신 날부터 3개월 안, "
            "그 사실을 아셨거나 아실 수 있었던 날부터 30일 안)",
            f"- 요청: 이메일 {email}, 전화 {phone}, 앱의 '고객센터'(카카오톡 채널). 요청을 받은 "
            "날부터 3영업일 안에 돌려드려요.",
            f"- 자세한 내용: 이용약관 {billing.app_url('/legal/terms')} · 환불 정책 "
            f"{billing.app_url('/legal/refund')}",
            "",
            f"{values['상호']} · 대표 {values['대표자']} · 주소 {shown(biz['address'])} · 전화 "
            f"{phone} · 이메일 {email}",
            f"사업자등록번호 {shown(biz['regNo'])} · 통신판매업 신고번호 "
            f"{shown(biz['mailOrderNo'])} · 개인정보 처리방침 "
            f"{billing.app_url('/legal/privacy_policy')}",
        ]
    )
    return TITLE.format(label=persona_label), body


async def after_payment(pay_id: str) -> None:
    """결제 하나의 계약 내용을 앱 알림(메일도)으로 남기고 contract_sent_at 을 적는다.

    구독 기간을 정한(commit 한) 뒤에 부른다. 따로 연 세션으로 하고, 예외는 기록만 남긴다(결제·구독은
    그대로다). 같은 결제로 두 번 불려도 알림은 하나다(dedupe contract:{pay_id}).
    """
    from app.db.session import SessionLocal

    try:
        async with SessionLocal() as db:
            pay = await db.get(Payment, pay_id)
            if pay is None or pay.status != "DONE" or pay.contract_sent_at is not None:
                return
            sub = await billing.get_subscription(db, pay.persona_id) if pay.persona_id else None
            user = await db.get(User, pay.user_id)
            if sub is None or user is None or sub.current_period_end is None:
                # 그사이 대화 상대를 지웠다(기간이 없다 — 결제 기록만 남는다)
                logger.warning("contract notice skipped: no subscription period")
                return
            persona = await db.get(Persona, pay.persona_id)
            # 이 결제의 요금제(올리기는 올린 요금제). 없앤 요금제면 None — 이름만 기록대로 보인다
            plan = billing.plans().get(pay.plan)
            title, body = render(pay, sub, plan, user, notices.persona_label(persona))
            # 이미 남긴 알림이 있으면(앞선 호출이 기록 전에 멈춤) 새로 만들지 않고 보낸 때만 적는다
            await notices.create(
                db,
                user.id,
                "contract",
                title,
                body,
                persona_id=pay.persona_id,
                dedupe_key=f"contract:{pay.id}",
                mail=True,
            )
            pay.contract_sent_at = datetime.now(UTC)
            await db.commit()
    except Exception:  # noqa: BLE001 — 알림을 못 남겨도 결제는 그대로다(로그를 보고 다시 보낸다)
        logger.exception("contract notice failed")
