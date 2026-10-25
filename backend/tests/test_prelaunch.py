"""결제 전 모드(PAYMENTS_OPEN=false): 구독 신청만 받고, 무료 체험에 한도가 없다.

결제가 열리면(PAYMENTS_OPEN=true) 원래 규칙으로 체험이 시작되고, 신청한 대화 상대가 결제하면
신청이 CONVERTED 로 바뀐다.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_billing import AGREE, API, _admin, _bare_ready, _me, _open, _pay, _pv
from test_persona_setup import BASICS, FakeAI, _signup, fake_ai  # noqa: F401

from app.core.config import get_settings
from app.db.models import SubscriptionRequest, User
from app.db.session import SessionLocal
from app.main import app
from app.services import billing


@pytest.fixture(autouse=True)
def prelaunch(monkeypatch):
    """.env 와 상관없이 결제 전 모드로 시작한다(시험 안에서 열기도 한다)."""
    monkeypatch.setattr(get_settings(), "payments_open", False)
    monkeypatch.setattr(get_settings(), "prelaunch_conversations", None)


def _user(email: str) -> User:
    async def go():
        async with SessionLocal() as db:
            return await db.scalar(select(User).where(User.email == email))

    return asyncio.run(go())


def _requests(pid: str) -> list[SubscriptionRequest]:
    async def go():
        async with SessionLocal() as db:
            return (
                await db.scalars(
                    select(SubscriptionRequest).where(SubscriptionRequest.persona_id == pid)
                )
            ).all()

    return asyncio.run(go())


def _chat(client: TestClient, h: dict, pid: str, n: int) -> list[dict]:
    cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
        "conversationId"
    ]
    out = []
    for i in range(n):
        sent = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": f"안녕 {i}"}
        )
        assert sent.status_code == 200, sent.text
        out.append(sent.json()["data"])
    return out


def test_prelaunch_is_free_and_unlimited_without_using_trials(
    fake_ai: FakeAI,  # noqa: F811
    monkeypatch,
):
    email = "prelaunch-free@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        me = _me(client, h)
        assert me["paymentsOpen"] is False and me["prelaunchConversations"] is None
        # 만드는 중인 대화 상대: 다 만들면 무료로 쓴다(체험 자리를 쓰지 않는다)
        draft = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        draft_view = next(p for p in _me(client, h)["personas"] if p["personaId"] == draft)
        assert draft_view["status"] == "NOT_STARTED" and draft_view["trialWillStart"] is True

        # 계정의 무료 체험 상한(1명)과 상관없이 다 만든 대화 상대 둘 다 쓴다
        first, second = _bare_ready(client, h), _bare_ready(client, h)
        for pid in (first, second):
            pv = _pv(client, h, pid)
            assert pv["status"] == "PRELAUNCH" and pv["active"] is True
            assert pv["plan"] == "trial" and pv["voice"] == "ivc"
            assert pv["conversationsLimit"] is None and pv["conversationsLeft"] is None
            assert pv["trialEndsAt"] is None and pv["textOnly"] is False
            assert pv["request"] is None
        assert _user(email).trials_used == 0
        assert _me(client, h)["trialsLeft"] == 1

        # 대화 횟수 제한이 없다(체험 상한을 넘겨도 목소리 대답 그대로)
        monkeypatch.setattr(get_settings(), "trial_conversations", 2)
        sent = _chat(client, h, first, 4)
        assert all(s["textOnly"] is False and s["conversationsLeft"] is None for s in sent)
        pv = _pv(client, h, first)
        assert pv["conversationsUsed"] == 4 and pv["status"] == "PRELAUNCH"

        # 운영 비용이 걱정되면 한 달 상한만 넣는다
        monkeypatch.setattr(get_settings(), "prelaunch_conversations", 5)
        assert _me(client, h)["prelaunchConversations"] == 5
        pv = _pv(client, h, first)
        assert pv["conversationsLimit"] == 5 and pv["conversationsLeft"] == 1
        # 다 만든 날부터 30일마다 다시 채워진다(최근 30일이 아니라)
        made = datetime.fromisoformat(pv["madeAt"].replace("Z", "+00:00"))
        reset = datetime.fromisoformat(pv["conversationsResetAt"].replace("Z", "+00:00"))
        assert reset - made == timedelta(days=30)
        last = _chat(client, h, first, 2)
        assert last[0]["conversationsLeft"] == 0 and last[1]["textOnly"] is True


def test_requests_create_switch_cancel_and_checkout_is_closed(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "prelaunch-request@example.com")
        pid = _bare_ready(client, h)

        made = client.post(
            f"{API}/billing/requests", headers=h, json={"personaId": pid, "plan": "memories"}
        )
        assert made.status_code == 200, made.text
        request = made.json()["data"]["request"]
        assert request["plan"] == "memories" and request["planName"] == "추억"
        assert request["status"] == "REQUESTED" and request["createdAt"]
        assert made.json()["data"]["personaId"] == pid

        # 다른 요금제로 바꾸면 같은 줄을 고친다(대화 상대마다 하나)
        switched = client.post(
            f"{API}/billing/requests", headers=h, json={"personaId": pid, "plan": "family"}
        )
        assert switched.json()["data"]["request"]["plan"] == "family"
        rows = _requests(pid)
        assert len(rows) == 1 and rows[0].plan == "family" and rows[0].status == "REQUESTED"

        # /billing/me 에도 보인다
        view = next(p for p in _me(client, h)["personas"] if p["personaId"] == pid)
        assert view["request"]["planName"] == "가족"

        # 바로 결제는 막는다(관리자도 같다)
        body = {"personaId": pid, "plan": "memories", "agreements": AGREE}
        closed = client.post(f"{API}/billing/checkout", headers=h, json=body)
        assert closed.status_code == 409
        assert closed.json()["detail"] == "지금은 결제가 열리기 전이라 구독 신청만 받아요."
        admin = _admin(client)
        admin_pid = client.get(f"{API}/billing/me", headers=admin).json()["data"]["personas"][0][
            "personaId"
        ]
        admin_try = client.post(
            f"{API}/billing/checkout",
            headers=admin,
            json={"personaId": admin_pid, "plan": "memories", "agreements": AGREE},
        )
        assert admin_try.status_code == 409

        # 취소하면 신청이 사라지고(CANCELED), 다시 신청할 수 있다
        canceled = client.delete(f"{API}/billing/requests/{pid}", headers=h)
        assert canceled.status_code == 200 and canceled.json()["data"]["request"] is None
        assert _requests(pid)[0].status == "CANCELED"
        again = client.delete(f"{API}/billing/requests/{pid}", headers=h)
        assert again.status_code == 200  # 신청이 없어도 그대로 돌려준다
        renewed = client.post(
            f"{API}/billing/requests", headers=h, json={"personaId": pid, "plan": "memories"}
        )
        assert renewed.json()["data"]["request"]["status"] == "REQUESTED"

        # 남의 대화 상대에는 신청할 수 없다
        other = _signup(client, "prelaunch-other@example.com")
        stranger = client.post(
            f"{API}/billing/requests", headers=other, json={"personaId": pid, "plan": "memories"}
        )
        assert stranger.status_code == 404


def test_premium_request_is_refused(fake_ai: FakeAI):  # noqa: F811
    """똑 닮은 목소리 요금제는 없앴다. 신청도 받지 않고, 연락처도 받지 않는다."""
    with TestClient(app) as client:
        h = _signup(client, "prelaunch-premium@example.com")
        pid = _bare_ready(client, h)
        refused = client.post(
            f"{API}/billing/requests",
            headers=h,
            json={"personaId": pid, "plan": "premium", "phone": "010-1234-5678"},
        )
        assert refused.status_code == 422
        assert _requests(pid) == []
        # 예전 화면이 연락처를 함께 보내도 그냥 버린다(신청은 그대로 받는다)
        ok = client.post(
            f"{API}/billing/requests",
            headers=h,
            json={"personaId": pid, "plan": "family", "phone": "010-1234-5678"},
        )
        assert ok.status_code == 200, ok.text
        assert "contactPhone" not in ok.json()["data"]["request"]
        assert _requests(pid)[0].contact_phone is None


def test_after_opening_trial_starts_and_payment_converts_request(
    fake_ai: FakeAI,  # noqa: F811
    monkeypatch,
):
    email = "prelaunch-open@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        assert _pv(client, h, pid)["status"] == "PRELAUNCH"
        client.post(f"{API}/billing/requests", headers=h, json={"personaId": pid, "plan": "family"})

        # 결제를 연다(PAYMENTS_OPEN=true 로 다시 켠 것과 같다)
        monkeypatch.setattr(get_settings(), "payments_open", True)
        me = _me(client, h)
        assert me["paymentsOpen"] is True
        pv = next(p for p in me["personas"] if p["personaId"] == pid)
        # 이제 원래 규칙으로 체험이 시작되고, 신청은 그대로 보인다(시작하기 안내)
        assert pv["status"] == "TRIAL" and pv["conversationsLimit"] == 30
        assert pv["request"]["plan"] == "family"
        assert _user(email).trials_used == 1
        # 결제가 열린 뒤에는 새로 신청하지 않고 바로 결제한다
        late = client.post(
            f"{API}/billing/requests", headers=h, json={"personaId": pid, "plan": "memories"}
        )
        assert late.status_code == 409
        assert late.json()["detail"] == "이제 바로 결제할 수 있어요. 요금제 화면에서 시작해 주세요."

        assert _pay(client, h, pid, "family").endswith("result=success")
        pv = _pv(client, h, pid)
        assert pv["status"] == "ACTIVE" and pv["plan"] == "family"
        assert pv["request"] is None
        assert _requests(pid)[0].status == "CONVERTED"


def test_requests_are_removed_with_persona_and_account(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "prelaunch-delete@example.com")
        first, second = _bare_ready(client, h), _bare_ready(client, h)
        for pid in (first, second):
            client.post(
                f"{API}/billing/requests", headers=h, json={"personaId": pid, "plan": "memories"}
            )
        assert client.delete(f"{API}/personas/{first}", headers=h).status_code == 200
        assert _requests(first) == []
        gone = client.request("DELETE", f"{API}/auth/me", headers=h, json={"confirm": "탈퇴"})
        assert gone.status_code == 200, gone.text
        assert _requests(second) == []


def test_prelaunch_cycle_counts_from_the_day_the_persona_was_made():
    from app.services.billing import prelaunch_cycle

    made = datetime(2026, 10, 14, 5, 0, tzinfo=UTC)
    # 만든 날 바로: 첫 30일
    assert prelaunch_cycle(made, made) == (made, made + timedelta(days=30))
    # 29일 뒤도 첫 30일, 30일째부터 다음 30일
    assert prelaunch_cycle(made, made + timedelta(days=29, hours=23))[0] == made
    start, end = prelaunch_cycle(made, made + timedelta(days=30))
    assert start == made + timedelta(days=30) and end == made + timedelta(days=60)
    start, _ = prelaunch_cycle(made, made + timedelta(days=95))
    assert start == made + timedelta(days=90)


def test_admin_sees_the_made_date_cycle_without_a_limit(fake_ai: FakeAI):  # noqa: F811
    """관리자도 만든 날부터 30일씩 센다(횟수 제한은 그대로 없음)."""
    with TestClient(app) as client:
        admin = _admin(client)
        pid = _bare_ready(client, admin)
        view = _pv(client, admin, pid)
        assert view["status"] == "ADMIN"
        assert view["conversationsLimit"] is None
        made = datetime.fromisoformat(view["madeAt"])
        reset = datetime.fromisoformat(view["conversationsResetAt"])
        assert reset - made == timedelta(days=30)


def test_payment_testers_get_a_test_checkout_while_payments_are_closed(
    fake_ai: FakeAI,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    # 결제가 닫혀 있어도 결제 시험 계정은 시험 결제창까지 간다(대소문자·빈칸은 가리지 않는다)
    testers = " Tester-PT@Example.com ,x@example.com"
    monkeypatch.setattr(get_settings(), "payments_test_emails", testers)
    with TestClient(app) as client:
        tester = _signup(client, "tester-pt@example.com")
        member = _signup(client, "member-pt@example.com")
        tpid = _bare_ready(client, tester)
        mpid = _bare_ready(client, member)
        assert _me(client, tester)["paymentsOpen"] is True
        assert _me(client, member)["paymentsOpen"] is False
        _open(client, tester, tpid, "family")  # 시험 결제창이 열린다
        closed = client.post(
            f"{API}/billing/checkout",
            headers=member,
            json={"personaId": mpid, "plan": "family", "agreements": AGREE},
        )
        assert closed.status_code == 409
        # 다른 회원은 그대로 구독 신청만, 시험 계정은 신청 대신 결제
        asked = client.post(
            f"{API}/billing/requests", headers=member, json={"personaId": mpid, "plan": "family"}
        )
        assert asked.status_code == 200, asked.text
        refused = client.post(
            f"{API}/billing/requests", headers=tester, json={"personaId": tpid, "plan": "family"}
        )
        assert refused.status_code == 409


def test_payment_testers_only_open_with_a_test_provider(monkeypatch: pytest.MonkeyPatch):
    class Live:
        test_mode = False

    class Test:
        test_mode = True

    tester, other = User(email="tester@example.com"), User(email="other@example.com")
    monkeypatch.setattr(get_settings(), "payments_test_emails", "tester@example.com")
    monkeypatch.setattr(billing, "provider", lambda: Test())
    assert billing.payments_open(tester) is True
    assert billing.payments_open(other) is False
    assert billing.payments_open(None) is False
    # 실제 돈이 오가는 결제(실제 키)면 시험 계정도 닫힌 그대로다
    monkeypatch.setattr(billing, "provider", lambda: Live())
    assert billing.payments_open(tester) is False
    monkeypatch.setattr(billing, "provider", lambda: None)
    assert billing.payments_open(tester) is False
