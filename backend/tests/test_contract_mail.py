"""결제 뒤 계약 내용 교부(전자상거래법 제13조 제2항 · WO 1-8 · SPEC §3.5)와 결제 창의 청약철회
안내·사업자 정보(WO 1-8 · WO 6 · SPEC §3.2).

- 계약 내용은 앱 알림(kind=contract)으로 늘 남고, 고객 메일이 켜져 있을 때만 메일로도 간다(.invalid
  주소에는 보내지 않는다). 첫 결제·올리기·갱신마다 하나씩, 같은 결제는 한 번만.
- 이용 기간 끝 = 다음 결제일의 전날.

사업자 값은 모두 지어낸 시험용이다(실제 주소·전화·등록번호를 쓰지 않는다).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from html import escape
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from test_billing import AGREE, _bare_ready, _pay, _payments, _pv, _renew, _set_sub
from test_persona_setup import FakeAI, _signup, fake_ai  # noqa: F401

from app.core.config import get_settings
from app.db.models import OwnerNotice, Payment, Subscription, User
from app.db.session import SessionLocal
from app.main import app
from app.services import billing, contract_mail, legal, mailer

API = "/api/v1"
FRONT = "https://app.example.test"
BIZ = {
    "business_name": "시험상호",
    "business_owner": "시험대표",
    "business_email": "help@example.test",
    "business_phone": "010-0000-0000",
    "business_reg_no": "000-00-00000",
    "business_address": "시험시 시험로 1, 101호",
    "mail_order_no": "제2026-서울용산-1112호",
}


@pytest.fixture(autouse=True)
def shop(monkeypatch):
    """결제가 열린 뒤 · 사업자 값은 시험용 · 웹 주소 고정."""
    s = get_settings()
    monkeypatch.setattr(s, "payments_open", True)
    monkeypatch.setattr(s, "frontend_url", FRONT)
    for key, value in BIZ.items():
        monkeypatch.setattr(s, key, value)
    return s


@pytest.fixture
def outbox(monkeypatch):
    """보내기를 건 고객·운영 메일(실제로 보내지 않는다)."""
    sent: list[tuple] = []

    def start(subject, text, recipients=None):
        sent.append((subject, text, recipients))
        return True

    monkeypatch.setattr(mailer, "_start", start)
    return sent


def _db(fn):
    async def go():
        async with SessionLocal() as db:
            return await fn(db)

    return asyncio.run(go())


def _contracts(email: str) -> list[OwnerNotice]:
    async def go(db):
        uid = await db.scalar(select(User.id).where(User.email == email))
        rows = await db.scalars(
            select(OwnerNotice)
            .where(OwnerNotice.user_id == uid, OwnerNotice.kind == "contract")
            .order_by(OwnerNotice.created_at)
        )
        return list(rows.all())

    return _db(go)


EXPECTED = """홍길동님, TalkTo 결제가 끝났어요. 아래 계약 내용을 보관해 주세요.

- 대화 상대: '할머니' 대화 상대
- 요금제: 추억 요금제(월 9,900원, 부가세 포함)
- 이번 결제: 9,900원 · 2026년 11월 5일 10:30 · 주문 번호 talkto_test_1 · 첫 결제
- 이용 기간: 2026년 11월 5일부터 2026년 12월 4일까지
- 다음 결제일: 2026년 12월 5일. 매달 같은 날 자동으로 결제돼요. 요금제 화면에서 언제든 해지할 수 있고, 해지해도 이용 기간이 끝나는 날까지 쓸 수 있어요.

결제 취소(청약철회)와 환불
- 결제하시면 바로 유료 이용이 시작돼요. 결제한 뒤 이 대화 상대와 대화를 시작하면(초대한 가족이 시작해도 같아요) 단순 변심에 의한 결제 취소(청약철회)가 제한돼요(전자상거래법 제17조 제2항 제5호).
- 다만 아래의 경우에는 전액 돌려드려요.
  1. 결제한 날부터 7일 안에 이 대화 상대와 한 번도 대화하지 않으셨을 때
  2. 무료 체험을 쓰지 않은 대화 상대를 처음 결제한 날부터 7일 안에 말씀하실 때(대화하셨더라도)
  3. 표시·광고나 안내한 내용, 계약 내용과 다르게 드렸을 때(받으신 날부터 3개월 안, 그 사실을 아셨거나 아실 수 있었던 날부터 30일 안)
- 요청: 이메일 help@example.test, 전화 010-0000-0000, 앱의 '고객센터'(카카오톡 채널). 요청을 받은 날부터 3영업일 안에 돌려드려요.
- 자세한 내용: 이용약관 https://app.example.test/#/legal/terms · 환불 정책 https://app.example.test/#/legal/refund

시험상호 · 대표 시험대표 · 주소 시험시 시험로 1, 101호 · 전화 010-0000-0000 · 이메일 help@example.test
사업자등록번호 000-00-00000 · 통신판매업 신고번호 제2026-서울용산-1112호 · 개인정보 처리방침 https://app.example.test/#/legal/privacy_policy"""  # noqa: E501


def _sample() -> tuple[Payment, Subscription, User]:
    # 한국 2026년 11월 5일 오전 10시 30분 결제
    paid = datetime(2026, 11, 5, 1, 30, tzinfo=UTC)
    pay = Payment(
        order_id="talkto_test_1", amount=9900, kind="first", plan="memories", approved_at=paid
    )
    sub = Subscription(
        plan="memories",
        current_period_start=paid,
        current_period_end=billing.add_month(paid),
        cancel_at_period_end=False,
    )
    user = User(name="홍길동", email="hong@example.test")
    return pay, sub, user


def test_render_is_the_final_wording_and_period_ends_the_day_before_next_payment():
    pay, sub, user = _sample()
    plan = billing.plans()["memories"]
    title, body = contract_mail.render(pay, sub, plan, user, "'할머니' 대화 상대")
    assert title == "결제 내용과 청약철회 안내 — '할머니' 대화 상대"
    assert body == EXPECTED

    # 해지 예정이면 다음 결제가 없다고 알린다
    sub.cancel_at_period_end = True
    _, body = contract_mail.render(pay, sub, plan, user, "'할머니' 대화 상대")
    assert (
        "- 다음 결제일: 없어요. 해지하셔서 2026년 12월 4일까지 쓰시고 그 뒤로는 결제되지 않아요."
        in body
    )
    # 올리기는 그 금액과 올린 요금제의 월 요금
    pay.kind, pay.amount, pay.plan = "upgrade", 13340, "family"
    sub.cancel_at_period_end = False
    _, body = contract_mail.render(pay, sub, billing.plans()["family"], user, "'엄마' 대화 상대")
    assert "- 요금제: 가족 요금제(월 29,900원, 부가세 포함)" in body
    assert (
        "- 이번 결제: 13,340원 · 2026년 11월 5일 10:30 · 주문 번호 talkto_test_1 · 요금제 올리기"
        in (body)
    )


def test_unset_business_values_say_pending(monkeypatch):
    s = get_settings()
    for key in ("business_address", "business_reg_no", "business_phone"):
        monkeypatch.setattr(s, key, None)
    pay, sub, user = _sample()
    _, body = contract_mail.render(
        pay, sub, billing.plans()["memories"], user, "'할머니' 대화 상대"
    )
    assert "주소 (준비 중)" in body and "사업자등록번호 (준비 중)" in body
    assert "전화 (준비 중)" in body


def test_contract_notice_for_first_payment_upgrade_and_renewal(fake_ai: FakeAI, outbox):  # noqa: F811
    email = f"contract-{uuid4().hex[:8]}@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        _pv(client, h, pid)
        _pay(client, h, pid, "memories")
        [first] = _payments(email)
        [note] = _contracts(email)
        assert note.dedupe_key == f"contract:{first.id}" and note.persona_id == pid
        assert note.title == "결제 내용과 청약철회 안내 — '할머니' 대화 상대"
        assert f"주문 번호 {first.order_id} · 첫 결제" in note.body
        assert note.action is None
        assert first.contract_sent_at is not None
        # 고객 메일이 꺼져 있으면(기본) 앱 알림만 남는다
        assert note.mailed_at is None and outbox == []
        # 앱 알림 목록에 보인다
        listed = client.get(f"{API}/notices", headers=h).json()["data"]
        assert [n["kind"] for n in listed] == ["contract"]

        # 요금제 올리기
        up = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": pid, "plan": "family", "agreements": AGREE, "mode": "change"},
        )
        assert up.status_code == 200, up.text
        upgrade = _payments(email)[-1]
        notes = _contracts(email)
        assert len(notes) == 2 and notes[-1].dedupe_key == f"contract:{upgrade.id}"
        assert "· 요금제 올리기" in notes[-1].body and "가족 요금제" in notes[-1].body

        # 갱신: 새 기간의 계약 내용
        _set_sub(pid, current_period_end=datetime.now(UTC) - timedelta(minutes=1))
        _renew()
        renew = _payments(email)[-1]
        assert renew.kind == "renew"
        notes = _contracts(email)
        assert len(notes) == 3 and "· 갱신" in notes[-1].body
        sub = _db(lambda db: billing.get_subscription(db, pid))
        end = billing._aware(sub.current_period_end).astimezone(billing.KST)
        assert f"- 다음 결제일: {end.year}년 {end.month}월 {end.day}일." in notes[-1].body

        # 같은 결제로 다시 불려도 하나뿐이다
        asyncio.run(contract_mail.after_payment(first.id))
        assert len(_contracts(email)) == 3

        # 알림은 남겼는데 보낸 때를 적기 전에 멈췄다 → 다시 불러도 알림은 그대로, 보낸 때만 적는다
        async def unsent(db):
            row = await db.get(Payment, first.id)
            row.contract_sent_at = None
            await db.commit()

        _db(unsent)
        asyncio.run(contract_mail.after_payment(first.id))
        assert len(_contracts(email)) == 3
        assert _db(lambda db: db.get(Payment, first.id)).contract_sent_at is not None


def test_contract_mail_goes_out_only_when_enabled(fake_ai: FakeAI, outbox, monkeypatch):  # noqa: F811
    email = f"contract-mail-{uuid4().hex[:8]}@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        _pv(client, h, pid)
        _pay(client, h, pid, "memories")
    [pay] = _payments(email)

    async def reset(db):
        await db.execute(delete(OwnerNotice).where(OwnerNotice.dedupe_key == f"contract:{pay.id}"))
        row = await db.get(Payment, pay.id)
        row.contract_sent_at = None
        await db.commit()

    # SES 운영 권한을 받고 대표가 켠 뒤(운영 · 보내는 주소 · USER_MAIL_ENABLED)
    s = get_settings()
    monkeypatch.setattr(s, "environment", "production")
    monkeypatch.setattr(s, "mail_from", "noreply@example.test")
    monkeypatch.setattr(s, "user_mail_enabled", True)
    _db(reset)
    asyncio.run(contract_mail.after_payment(pay.id))
    [note] = _contracts(email)
    assert note.mailed_at is not None
    [(subject, text, to)] = outbox
    assert to == [email]
    assert subject == "[TalkTo] 결제 내용과 청약철회 안내 — '할머니' 대화 상대"
    assert text.startswith(note.body) and f"앱에서 확인해 주세요: {FRONT}" in text

    # 이메일 없이 가입한 소셜 계정의 빈 자리 주소(.invalid)에는 보내지 않고 앱 알림만 남긴다
    placeholder = f"kakao-{uuid4().hex[:8]}@social.talkto.invalid"

    async def rename(db):
        user = await db.get(User, pay.user_id)
        user.email = placeholder
        await db.commit()

    _db(rename)
    _db(reset)
    asyncio.run(contract_mail.after_payment(pay.id))
    [note] = _contracts(placeholder)
    assert note.mailed_at is None and len(outbox) == 1
    assert _db(lambda db: db.get(Payment, pay.id)).contract_sent_at is not None


def _checkout(provider) -> str:
    return provider.checkout_html(
        customer_key="ck",
        plan=billing.plans()["memories"],
        amount=9900,
        beta=False,
        success_url="https://s?session=1",
        fail_url="https://f?session=1",
        customer_name="홍길동",
        customer_email=None,
    )


@pytest.mark.parametrize(
    ("provider", "button"),
    [
        (billing.TossProvider("test_ck_x", "test_sk_y"), 'id="pay"'),
        (billing.MockProvider(), "연습으로 결제하기"),
    ],
)
def test_checkout_page_shows_withdrawal_notice_and_business_line(provider, button, monkeypatch):
    monkeypatch.setattr(get_settings(), "business_address", "시험시 <b>시험로</b> 1")
    html = _checkout(provider)
    # 결제 단추 위에 청약철회 안내(결제 직전 한 번 더)
    notice = escape(legal.PAYMENT_NOTICE)
    assert f'<p class="withdrawal">{notice}</p>' in html
    assert html.index(notice) < html.index(button)
    # 아래쪽에 사업자 정보 한 줄과 개인정보 처리방침(값은 이스케이프)
    line = (
        "상호 시험상호 · 대표 시험대표 · 사업장 주소 시험시 &lt;b&gt;시험로&lt;/b&gt; 1 · "
        "전화 010-0000-0000 · 이메일 help@example.test · 사업자등록번호 000-00-00000 · "
        "통신판매업 신고번호 제2026-서울용산-1112호 · 호스팅 Amazon Web Services(AWS)"
    )
    assert line in html and "<b>시험로" not in html
    assert html.index(button) < html.index(line)
    assert f'href="{FRONT}/#/legal/privacy_policy"' in html and ">개인정보 처리방침</a>" in html
    # 비운 값은 빼고 보인다
    monkeypatch.setattr(get_settings(), "business_reg_no", None)
    assert "사업자등록번호" not in _checkout(provider)
