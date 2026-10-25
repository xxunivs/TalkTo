"""청약철회·환불 판단 기록(2026-10-22 법률 자문 · WO 1-7 · SPEC §3.3·§3.4).

- 결제 후 첫 대화: 이번 이용 기간의 결제마다(첫 결제·갱신·같은 기간의 올리기), 초대한 가족의 대화도
  센다. 대답을 받지 못한 대화·지난 기간 결제는 찍지 않는다.
- 체험 사용: 첫 결제 때 그 대화 상대가 체험 기간에 실제로 대화했는지(화면만 열어 시작된 체험·결제 전
  무료 대화는 '안 씀').
- 관리자 환불 화면: 환불 짐작 세 가지, 손으로 한 환불 기록은 한 번만, 남은 날 일할 계산.

모든 자료는 지어낸 시험용이다. 결제는 시험 결제(MockProvider)다.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from legal_gates import declare_bereaved
from sqlalchemy import select
from test_billing import (
    AGREE,
    _admin,
    _backdate_conversations,
    _bare_ready,
    _pay,
    _payments,
    _pv,
    _renew,
    _set_sub,
    _use_conversations,
)
from test_persona_setup import FakeAI, _signup, fake_ai  # noqa: F401
from test_sharing import _accept, _link, _persona, _room, _say
from test_sharing import _signup as _share_signup

from app.core.config import get_settings
from app.db.models import Payment, Persona, User
from app.db.session import SessionLocal
from app.main import app
from app.services import ai as ai_module
from app.services import billing

API = "/api/v1"


@pytest.fixture(autouse=True)
def payments_open(monkeypatch):
    """결제가 열린 뒤의 흐름을 본다(지금 운영은 닫혀 있다 — 결제 시험 계정만 연다)."""
    monkeypatch.setattr(get_settings(), "payments_open", True)


def _email(tag: str) -> str:
    return f"{tag}-{uuid4().hex[:8]}@example.com"


def _db(fn):
    async def go():
        async with SessionLocal() as db:
            return await fn(db)

    return asyncio.run(go())


def _set_payment(pay_id: str, **values) -> None:
    async def go(db):
        pay = await db.get(Payment, pay_id)
        for key, value in values.items():
            setattr(pay, key, value)
        await db.commit()

    _db(go)


def _uid(email: str) -> str:
    return _db(lambda db: db.scalar(select(User.id).where(User.email == email)))


def _saved_card(client: TestClient, h: dict, pid: str, plan: str = "memories") -> None:
    """등록해 둔 카드로 바로 시작(결제 창 없이)."""
    started = client.post(
        f"{API}/billing/checkout",
        headers=h,
        json={"personaId": pid, "plan": plan, "agreements": AGREE, "mode": "saved_card"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["data"]["started"]


def _upgrade(client: TestClient, h: dict, pid: str) -> None:
    up = client.post(
        f"{API}/billing/checkout",
        headers=h,
        json={"personaId": pid, "plan": "family", "agreements": AGREE, "mode": "change"},
    )
    assert up.status_code == 200, up.text
    assert up.json()["data"]["changed"]


def _chat(client: TestClient, h: dict, pid: str, text: str = "할머니"):
    cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
        "conversationId"
    ]
    return client.post(
        f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": text}
    )


def test_trial_used_is_true_only_when_the_trial_was_chatted(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        # 화면을 열어 체험이 시작됐지만 대화는 없었다 → 쓰지 않음(전액 환불 대상이 될 수 있다)
        e1 = _email("trial-open")
        h1 = _signup(client, e1)
        opened = _bare_ready(client, h1)
        assert _pv(client, h1, opened)["status"] == "TRIAL"
        _pay(client, h1, opened, "memories")
        # 체험 자리가 없는 대화 상대(체험이 없다) → 쓰지 않음
        no_trial = _bare_ready(client, h1, relation="어머니", addressTerm="엄마")
        assert _pv(client, h1, no_trial)["status"] == "NO_TRIAL"
        _saved_card(client, h1, no_trial)

        # 결제가 열리기 전 무료 대화(체험이 시작되기 전)만 있었다 → 쓰지 않음
        e2 = _email("trial-prelaunch")
        h2 = _signup(client, e2)
        early = _bare_ready(client, h2)
        _use_conversations(e2, early, 3)
        _backdate_conversations(early, timedelta(days=10))
        assert _pv(client, h2, early)["status"] == "TRIAL"
        _pay(client, h2, early, "memories")

        # 체험 기간에 대화했다 → 썼음
        e3 = _email("trial-used")
        h3 = _signup(client, e3)
        used = _bare_ready(client, h3)
        assert _pv(client, h3, used)["status"] == "TRIAL"
        _use_conversations(e3, used, 1)
        _pay(client, h3, used, "memories")

    first = {p.persona_id: p for p in _payments(e1)}
    assert first[opened].kind == "first" and first[opened].trial_used is False
    assert first[no_trial].trial_used is False
    [prelaunch] = _payments(e2)
    assert prelaunch.trial_used is False
    [chatted] = _payments(e3)
    assert chatted.trial_used is True


def test_first_chat_after_payment_is_stamped_per_period(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    email = _email("first-chat")
    with TestClient(app, raise_server_exceptions=False) as client:
        h = _signup(client, email)
        uid = _uid(email)
        pid = _bare_ready(client, h)
        declare_bereaved(client, pid)
        _pv(client, h, pid)
        _pay(client, h, pid, "memories")
        [first] = _payments(email)
        assert first.first_chat_at is None and first.trial_used is False

        # 대답을 받지 못한 대화는 찍지 않는다
        real_reply = ai_module.AIService.reply

        async def broken(self, **kw):
            raise ai_module.AIServerError(503)

        monkeypatch.setattr(ai_module.AIService, "reply", broken)
        assert _chat(client, h, pid).status_code >= 400
        monkeypatch.setattr(ai_module.AIService, "reply", real_reply)
        [first] = _payments(email)
        assert first.first_chat_at is None

        # 대답을 받으면 찍는다(만든 분). 다음 대화는 바꾸지 않는다
        assert _chat(client, h, pid).status_code == 200
        [first] = _payments(email)
        assert first.first_chat_at is not None
        assert (first.first_chat_user_id, first.first_chat_member) == (uid, False)
        stamp = first.first_chat_at
        assert _chat(client, h, pid, "또 왔어요").status_code == 200
        assert _payments(email)[0].first_chat_at == stamp

        # 같은 기간의 요금제 올리기도 따로 낸 결제라 따로 찍는다
        _upgrade(client, h, pid)
        first, upgrade = _payments(email)
        assert upgrade.kind == "upgrade" and upgrade.first_chat_at is None
        assert upgrade.trial_used is None  # 체험 스냅숏은 첫 결제에만
        assert _chat(client, h, pid).status_code == 200
        first, upgrade = _payments(email)
        assert upgrade.first_chat_at is not None and first.first_chat_at == stamp

        # 갱신하면 새 기간의 결제를 새로 찍는다
        _set_sub(pid, current_period_end=datetime.now(UTC) - timedelta(minutes=1))
        _renew()
        renew = _payments(email)[-1]
        assert renew.kind == "renew" and renew.first_chat_at is None
        assert _chat(client, h, pid).status_code == 200
        assert _payments(email)[-1].first_chat_at is not None

        # 지난 기간 결제는 찍지 않는다(결제하고 대화 없이 기간이 지났다)
        quiet_email = _email("quiet")
        hq = _signup(client, quiet_email)
        quiet = _bare_ready(client, hq)
        declare_bereaved(client, quiet)
        _pv(client, hq, quiet)
        _pay(client, hq, quiet, "memories")
        [old] = _payments(quiet_email)
        long_ago = datetime.now(UTC) - timedelta(days=31)
        _set_payment(old.id, approved_at=long_ago)
        _set_sub(
            quiet,
            current_period_start=long_ago,
            current_period_end=datetime.now(UTC) - timedelta(minutes=5),
        )
        _renew()
        assert _chat(client, hq, quiet).status_code == 200
        old, renewed = _payments(quiet_email)
        assert old.first_chat_at is None
        assert renewed.kind == "renew" and renewed.first_chat_at is not None


def test_family_member_chat_counts_as_first_chat(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        owner_h, owner_id, owner_email = _share_signup(client, "김지은")
        pid = _persona(owner_id)
        declare_bereaved(client, pid)
        _pv(client, owner_h, pid)
        _pay(client, owner_h, pid, "family")
        token, _ = _link(client, owner_h, pid)
        member_h, member_id, _ = _share_signup(client, "박철수")
        joined = _accept(client, member_h, token)
        assert joined.status_code == 201, joined.text
        said = _say(client, member_h, _room(client, member_h, pid))
        assert said.status_code == 200, said.text
    [pay] = _payments(owner_email)
    assert pay.first_chat_member is True and pay.first_chat_user_id == member_id


def _row(**values) -> Payment:
    base = {
        "id": str(uuid4()),
        "user_id": str(uuid4()),
        "persona_id": str(uuid4()),
        "plan": "memories",
        "provider": "mock",
        "order_id": f"o_{uuid4().hex[:8]}",
        "amount": 9900,
        "status": "DONE",
        "kind": "first",
        # 한국 10월 1일 오후 3시
        "approved_at": datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
        "first_chat_at": None,
        "trial_used": False,
    }
    return Payment(**{**base, **values})


def test_refund_hint_three_cases():
    # 7일은 결제한 날을 빼고 센다: 10월 8일이 끝날 때(한국)까지
    end = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)
    assert billing.refund_deadline(datetime(2026, 10, 1, 6, 0, tzinfo=UTC)) == end
    inside, after = end - timedelta(minutes=1), end + timedelta(minutes=1)
    pay = _row()

    hint = billing.refund_hint(pay, first_paid_id=pay.id, at=inside)
    assert hint["code"] == "no_chat_7d"
    assert hint["text"] == "결제 후 7일 안이고 대화가 없어요 — 전액 환불 대상"
    assert billing.refund_hint(pay, first_paid_id=pay.id, at=after)["code"] == "none"

    pay.first_chat_at = datetime(2026, 10, 2, tzinfo=UTC)
    hint = billing.refund_hint(pay, first_paid_id=pay.id, at=inside)
    assert hint["code"] == "no_trial_7d"
    assert hint["text"] == "체험을 쓰지 않은 대화 상대의 첫 결제 7일 안 — 대화했어도 전액 환불 대상"
    # 그 대화 상대의 첫 결제가 아니거나, 체험을 썼거나, 7일이 지났으면 일반 규칙
    assert billing.refund_hint(pay, first_paid_id="other", at=inside)["code"] == "none"
    assert billing.refund_hint(pay, first_paid_id=pay.id, at=after)["code"] == "none"
    pay.trial_used = True
    hint = billing.refund_hint(pay, first_paid_id=pay.id, at=inside)
    assert hint == {
        "code": "none",
        "text": "일반 환불 사유 없음 — 표시·광고와 다르게 드린 경우(받은 날부터 3개월, 안 날부터 "
        "30일)는 따로 판단",
        "until": None,
    }
    # 갱신·올리기도 결제마다 '7일 안 대화 없음'을 본다. 결제가 안 된 기록은 대상이 아니다
    assert billing.refund_hint(_row(kind="renew"), first_paid_id=None, at=inside)["code"] == (
        "no_chat_7d"
    )
    assert billing.refund_hint(_row(kind="upgrade"), first_paid_id=None, at=inside)["code"] == (
        "no_chat_7d"
    )
    assert billing.refund_hint(_row(status="FAILED"), first_paid_id=None, at=inside)["code"] == (
        "none"
    )


def test_prorated_refund_counts_each_payment_over_what_it_covers(fake_ai: FakeAI):  # noqa: F811
    email = _email("prorated")
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        _pv(client, h, pid)
        _pay(client, h, pid, "memories")
    [first] = _payments(email)
    start = datetime.now(UTC) - timedelta(days=20)
    end = start + timedelta(days=30)
    _set_sub(pid, current_period_start=start, current_period_end=end)
    _set_payment(first.id, approved_at=start, amount=9000)

    async def add_upgrade(db):
        pay = Payment(
            user_id=first.user_id,
            persona_id=pid,
            plan="family",
            provider="mock",
            order_id=f"talkto_test_{uuid4().hex[:8]}",
            amount=2000,
            status="DONE",
            kind="upgrade",
            approved_at=start + timedelta(days=10),
        )
        db.add(pay)
        await db.commit()
        return pay.id

    upgrade_id = _db(add_upgrade)
    at = start + timedelta(days=20)
    got = _db(lambda db: billing.prorated_refund(db, pid, at))
    # 첫 결제 9,000원 × 남은 10일/30일 = 3,000원 + 올리기 2,000원 × 남은 10일/덮는 20일 = 1,000원
    assert got == {
        "paymentId": first.id,
        "paymentIds": [first.id, upgrade_id],
        "amount": 11000,
        "remainingDays": 10,
        "periodDays": 30,
        "refund": 4000,
    }
    # 이미 돌려준 금액은 뺀다(원 단위 내림)
    _set_payment(first.id, refund_amount=500)
    got = _db(lambda db: billing.prorated_refund(db, pid, at + timedelta(seconds=1)))
    assert got["refund"] == 3499
    # 기간이 끝났거나 구독이 없으면 없다
    assert _db(lambda db: billing.prorated_refund(db, pid, end)) is None
    assert _db(lambda db: billing.prorated_refund(db, str(uuid4()), at)) is None


def test_first_payment_approved_earlier_still_counts_in_its_period(
    fake_ai: FakeAI,  # noqa: F811
    monkeypatch,
):
    """결제 회사에 다시 묻거나 조회로 정한 결과는 승인 시각이 몇 분 앞설 수 있다(토스 60초 기다림 뒤
    같은 키로 다시 묻기). 기간을 승인 시각에 열어 그 결제도 첫 대화·일할 환불에 든다."""
    approved = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=90)
    real_charge = billing.MockProvider.charge

    async def late(self, **kw):
        result = await real_charge(self, **kw)
        result.approved_at = approved
        return result

    monkeypatch.setattr(billing.MockProvider, "charge", late)
    email = _email("late-approval")
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        declare_bereaved(client, pid)
        _pv(client, h, pid)
        _pay(client, h, pid, "memories")
        sub = _db(lambda db: billing.get_subscription(db, pid))
        assert billing._aware(sub.current_period_start) == approved
        [pay] = _payments(email)
        assert billing._aware(pay.approved_at) == approved and pay.first_chat_at is None

        assert _chat(client, h, pid).status_code == 200
        [pay] = _payments(email)
        assert pay.first_chat_at is not None

    got = _db(lambda db: billing.prorated_refund(db, pid, datetime.now(UTC)))
    assert got is not None and got["paymentIds"] == [pay.id]
    # 이제 막 결제했으니 거의 다 돌려준다(남은 초 / 덮는 초, 원 단위 내림)
    assert pay.amount - 10 <= got["refund"] <= pay.amount


def test_admin_payments_list_and_refund_record(fake_ai: FakeAI):  # noqa: F811
    email = _email("refund-admin")
    with TestClient(app) as client:
        h = _signup(client, email)
        uid = _uid(email)
        pid = _bare_ready(client, h)
        _pv(client, h, pid)
        _pay(client, h, pid, "memories")
        gone = _bare_ready(client, h, relation="어머니", addressTerm="엄마")
        _saved_card(client, h, gone)
        assert client.delete(f"{API}/personas/{gone}", headers=h).status_code == 200
        admin = _admin(client)

        # 관리자만
        assert client.get(f"{API}/admin/payments", headers=h).status_code == 403
        listed = client.get(f"{API}/admin/payments", headers=admin, params={"userId": uid})
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["meta"]["total"] == 2
        rows = {r["personaId"]: r for r in body["data"]}
        row = rows[pid]
        assert row["personaLabel"] == "'할머니' 대화 상대"
        assert rows[gone]["personaLabel"] == "지운 대화 상대"
        assert row["ownerMasked"].startswith(email[:2]) and "***@example.com" in row["ownerMasked"]
        assert email not in str(body)  # 전체 이메일은 싣지 않는다
        assert (row["kind"], row["status"], row["trialUsed"]) == ("first", "DONE", False)
        assert row["firstChatAt"] is None and row["firstChatBy"] is None
        assert row["refundHint"]["code"] == "no_chat_7d"
        assert row["periodStart"] and row["periodEnd"] and row["chatsBeforePayment"] == 0
        assert row["personaFirstPaidAt"] and row["trialEndsAt"]
        assert row["contractSentAt"]  # 계약 내용 알림을 남겼다

        # 주문 번호·대화 상대로 거르기, 쪽 나누기
        by_order = client.get(
            f"{API}/admin/payments", headers=admin, params={"q": row["orderId"][-8:]}
        ).json()
        assert [r["id"] for r in by_order["data"]] == [row["id"]]
        by_persona = client.get(f"{API}/admin/payments", headers=admin, params={"personaId": pid})
        assert by_persona.json()["meta"]["total"] == 1
        page = client.get(
            f"{API}/admin/payments", headers=admin, params={"userId": uid, "size": 1, "page": 2}
        ).json()
        assert len(page["data"]) == 1 and page["meta"]["total"] == 2

        # 가족·만든 분 대화가 찍히면 그대로 보인다
        _set_payment(row["id"], first_chat_at=datetime.now(UTC), first_chat_member=True)
        again = client.get(f"{API}/admin/payments", headers=admin, params={"personaId": pid})
        row = again.json()["data"][0]
        assert row["firstChatBy"] == "초대한 가족"
        # 체험을 쓰지 않은 첫 결제라 대화했어도 7일 안이면 전액 환불 대상
        assert row["refundHint"]["code"] == "no_trial_7d"

        # 손으로 환불한 뒤 기록: 결제 금액보다 많이는 안 되고, 한 번만
        url = f"{API}/admin/payments/{row['id']}/refund"
        assert (
            client.post(url, headers=h, json={"amount": 100, "reason": "other"}).status_code == 403
        )
        too_much = client.post(
            url, headers=admin, json={"amount": row["amount"] + 1, "reason": "no_trial_7d"}
        )
        assert too_much.status_code == 400
        bad = client.post(url, headers=admin, json={"amount": 100, "reason": "because"})
        assert bad.status_code == 422
        done = client.post(
            url,
            headers=admin,
            json={"amount": row["amount"], "reason": "no_trial_7d", "note": "고객센터 요청"},
        )
        assert done.status_code == 200, done.text
        saved = done.json()["data"]
        assert saved["refundAmount"] == row["amount"] and saved["refundedAt"]
        assert saved["refundNote"] == "no_trial_7d: 고객센터 요청"
        twice = client.post(url, headers=admin, json={"amount": 100, "reason": "other"})
        assert twice.status_code == 409
        missing = client.post(
            f"{API}/admin/payments/{uuid4()}/refund",
            headers=admin,
            json={"amount": 100, "reason": "other"},
        )
        assert missing.status_code == 404
        # 고객의 결제 내역에도 돌려드린 금액이 보인다
        history = client.get(f"{API}/billing/payments", headers=h).json()["data"]
        refunded = next(p for p in history if p["id"] == row["id"])
        assert refunded["refundAmount"] == row["amount"] and refunded["refundedAt"]
    assert _db(lambda db: db.get(Persona, gone)) is None
