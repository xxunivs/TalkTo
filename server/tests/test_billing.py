"""요금제·무료 체험·구독 결제(시험 결제)·초기 가족 요금·대화 횟수·녹음 추가.

결제·체험·대화 횟수는 모두 페르소나마다 따로다. 초기 가족 요금만 가족(계정) 단위다.
"""

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from fastapi.testclient import TestClient
from legal_gates import declare_bereaved
from sqlalchemy import select
from test_persona_setup import ANSWERS, BASICS, FakeAI, _signup, _wait, _wav, fake_ai  # noqa: F401

from app.core.config import get_settings
from app.db.models import (
    Payment,
    Persona,
    SetupStep,
    Subscription,
    UsageEvent,
    User,
)
from app.db.session import SessionLocal
from app.main import app
from app.services import billing

API = "/api/v1"
FRONT = "http://localhost:8090/"
# 결제 직전 확인 두 가지(정기결제·결제 취소 제한)
AGREE = ["recurring", "withdrawal_limit"]


@pytest.fixture(autouse=True)
def payments_open(monkeypatch):
    """이 파일은 결제가 열린 뒤의 흐름(체험·결제)을 본다. 결제 전 모드는 test_prelaunch.py."""
    monkeypatch.setattr(get_settings(), "payments_open", True)


def _set_user(email: str, **values) -> None:
    async def go():
        async with SessionLocal() as db:
            user = await db.scalar(select(User).where(User.email == email))
            for key, value in values.items():
                setattr(user, key, value)
            await db.commit()

    asyncio.run(go())


def _set_persona(pid: str, **values) -> None:
    async def go():
        async with SessionLocal() as db:
            persona = await db.get(Persona, pid)
            for key, value in values.items():
                setattr(persona, key, value)
            await db.commit()

    asyncio.run(go())


def _set_sub(pid: str, **values) -> None:
    async def go():
        async with SessionLocal() as db:
            sub = await db.scalar(select(Subscription).where(Subscription.persona_id == pid))
            for key, value in values.items():
                setattr(sub, key, value)
            await db.commit()

    asyncio.run(go())


def _payments(email: str) -> list[Payment]:
    async def go():
        async with SessionLocal() as db:
            user = await db.scalar(select(User).where(User.email == email))
            return (
                await db.scalars(
                    select(Payment).where(Payment.user_id == user.id).order_by(Payment.created_at)
                )
            ).all()

    return asyncio.run(go())


def _admin(client: TestClient) -> dict:
    """처음 만든 관리자 계정으로 로그인한 머리글."""
    settings = get_settings()
    response = client.post(
        f"{API}/auth/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['bearerToken']}"}


def _me(client: TestClient, h: dict) -> dict:
    response = client.get(f"{API}/billing/me", headers=h)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _pv(client: TestClient, h: dict, pid: str) -> dict:
    """페르소나 하나의 요금제 상태."""
    response = client.get(f"{API}/billing/personas/{pid}", headers=h)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _bare_ready(client: TestClient, h: dict, **basics) -> str:
    """녹음 없이 '다 만든' 페르소나(결제 시험용). 체험은 다 만든 뒤 처음 볼 때 시작한다."""
    pid = client.post(f"{API}/personas", headers=h, json={**BASICS, **basics}).json()["data"]["id"]
    _set_persona(pid, setup_step=SetupStep.READY)
    return pid


def _open(client: TestClient, h: dict, pid: str, plan: str) -> tuple[str, str, str]:
    """결제 창을 열기만 한다. (session, customerKey, authKey)."""
    started = client.post(
        f"{API}/billing/checkout",
        headers=h,
        json={"personaId": pid, "plan": plan, "agreements": AGREE, "returnTo": FRONT},
    )
    assert started.status_code == 200, started.text
    url = started.json()["data"]["checkoutUrl"]
    page = client.get(url.replace("http://localhost:8000", ""))
    assert page.status_code == 200 and "연습으로 결제하기" in page.text
    session = parse_qs(urlparse(url).query)["session"][0]
    ck = page.text.split("customerKey=")[1].split("&")[0]
    auth = page.text.split("authKey=")[1].split("'")[0]
    return session, ck, auth


def _pay(client: TestClient, h: dict, pid: str, plan: str) -> str:
    """시험 결제 창을 열고 '연습으로 결제하기'를 누른 것처럼 돌아온다. 돌아간 웹 주소를 준다."""
    session, ck, auth = _open(client, h, pid, plan)
    back = client.get(
        f"{API}/billing/callback/success",
        params={"session": session, "customerKey": ck, "authKey": auth},
        follow_redirects=False,
    )
    assert back.status_code == 302
    return back.headers["location"]


def _renew() -> None:
    async def go():
        async with SessionLocal() as db:
            await billing.renew_due(db)

    asyncio.run(go())


def test_trial_then_subscribe_upgrade_downgrade_cancel(fake_ai: FakeAI):  # noqa: F811
    email = "pay@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        me = _me(client, h)
        assert me["provider"] == "mock" and me["testMode"] and me["personas"] == []
        assert me["trialsLeft"] == 1 and me["cardLabel"] is None
        # 요금제는 추억·가족 둘(똑 닮은 목소리 요금제는 없앴다)
        assert [p["id"] for p in me["plans"]] == ["trial", "memories", "family"]
        assert {p["voice"] for p in me["plans"]} == {"ivc"}
        plans = {p["id"]: p for p in me["plans"]}
        # 정식 요금과 초기 가족 요금(첫 100가족 · 12개월)을 함께 보여 준다
        assert (plans["memories"]["price"], plans["memories"]["chargePrice"]) == (9900, 4900)
        assert (plans["family"]["price"], plans["family"]["chargePrice"]) == (29900, 14900)
        assert plans["family"]["conversations"] == 300 and plans["family"]["beta"]
        assert plans["memories"]["conversations"] == 50 and plans["trial"]["conversations"] == 30

        # 다 만들기 전에는 체험이 시작되지 않고, 결제도 고를 수 없다
        draft = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        assert _pv(client, h, draft)["status"] == "NOT_STARTED"
        early = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": draft, "plan": "memories", "agreements": AGREE},
        )
        assert early.status_code == 409
        # 다 만든 페르소나는 처음 볼 때부터 7일 체험
        pid = _bare_ready(client, h)
        pv = _pv(client, h, pid)
        assert pv["plan"] == "trial" and pv["active"] and pv["status"] == "TRIAL"
        assert pv["title"] == "할머니" and BASICS["name"] not in str(pv)  # 성함은 내보내지 않는다
        assert pv["conversationsLimit"] == 30 and pv["conversationsLeft"] == 30
        trial_end = datetime.fromisoformat(pv["trialEndsAt"])
        assert timedelta(days=6, hours=23) < trial_end - datetime.now(UTC) <= timedelta(days=7)
        assert _me(client, h)["trialsLeft"] == 0  # 계정 하나에 체험 1개

        # 체험이 끝나면 대화를 멈춘다(만든 것은 그대로)
        _set_persona(pid, trial_ends_at=datetime.now(UTC) - timedelta(minutes=1))
        assert _pv(client, h, pid)["status"] == "EXPIRED"

        # 결제 취소: 그 페르소나 요금제 화면으로 돌아간다
        session = _open(client, h, pid, "memories")[0]
        failed = client.get(
            f"{API}/billing/callback/fail",
            params={"session": session, "code": "USER_CANCEL", "message": "x"},
            follow_redirects=False,
        )
        assert unquote(failed.headers["location"]).endswith(
            f"#/plans?persona={pid}&error=결제를 취소했어요."
        )

        # Memories 구독: 첫 달 결제(초기 가족 요금)
        assert _pay(client, h, pid, "memories").endswith(f"#/plans?persona={pid}&result=success")
        pv = _pv(client, h, pid)
        assert pv["plan"] == "memories" and pv["status"] == "ACTIVE" and pv["voice"] == "ivc"
        assert pv["subscription"]["cardLabel"] == "연습용 카드"
        assert pv["conversationsLimit"] == 50 and pv["conversationsResetAt"]
        assert [(p.amount, p.status) for p in _payments(email)] == [(4900, "DONE")]
        me = _me(client, h)
        beta_until = datetime.fromisoformat(me["betaUntil"])
        assert timedelta(days=360) < beta_until - datetime.now(UTC) < timedelta(days=370)
        assert me["cardLabel"] == "연습용 카드"
        # 만들기 전 페르소나는 그대로(결제는 페르소나마다)
        assert _pv(client, h, draft)["status"] == "NOT_STARTED"

        # 가족으로 올리기: 카드가 있으니 바로, 남은 날만큼 차액(내는 값 기준 14,900 - 4,900)
        up = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": pid, "plan": "family", "agreements": AGREE},
        )
        assert up.status_code == 200, up.text
        assert up.json()["data"]["changed"] is True
        charged = up.json()["data"]["charged"]
        assert 8000 < charged <= 14900 - 4900
        pv = _pv(client, h, pid)
        assert pv["plan"] == "family" and pv["voice"] == "ivc"
        # 없앤 요금제는 고를 수 없다
        gone = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": pid, "plan": "premium", "agreements": AGREE},
        )
        assert gone.status_code == 422

        # 내리기는 이번 기간이 끝날 때
        down = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": pid, "plan": "memories", "agreements": AGREE},
        )
        assert down.json()["data"]["scheduled"] == "memories"
        pv = _pv(client, h, pid)
        assert pv["plan"] == "family" and pv["nextPlan"] == "memories"

        # 기간이 끝나면 Memories 로 갱신 결제
        _set_sub(pid, current_period_end=datetime.now(UTC) - timedelta(minutes=1))
        _renew()
        pv = _pv(client, h, pid)
        assert pv["plan"] == "memories" and pv["nextPlan"] is None and pv["status"] == "ACTIVE"
        assert [p.amount for p in _payments(email)][-1] == 4900  # 초기 요금 기간 안

        # 해지: 이번 기간은 쓰고, 끝나면 멈춘다. 다시 시작도 된다
        body = {"personaId": pid}
        assert client.post(f"{API}/billing/cancel", headers=h, json=body).status_code == 200
        assert _pv(client, h, pid)["status"] == "CANCELING"
        assert client.post(f"{API}/billing/resume", headers=h, json=body).status_code == 200
        assert _pv(client, h, pid)["status"] == "ACTIVE"
        client.post(f"{API}/billing/cancel", headers=h, json=body)
        _set_sub(pid, current_period_end=datetime.now(UTC) - timedelta(minutes=1))
        _renew()
        assert _pv(client, h, pid)["status"] == "EXPIRED"
        assert len(_payments(email)) == 3  # 해지했으니 더 결제하지 않는다

        history = client.get(f"{API}/billing/payments", headers=h).json()["data"]
        assert [p["planName"] for p in history] == ["추억", "가족", "추억"]
        assert {p["personaTitle"] for p in history} == {"할머니"}


def test_each_persona_pays_separately_with_saved_card(fake_ai: FakeAI):  # noqa: F811
    """결제는 페르소나마다. 등록한 카드로 다른 페르소나도 바로 시작하고, 초기 요금은 가족 단위."""
    email = "two-personas@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        grandma = _bare_ready(client, h)
        mom = _bare_ready(client, h, relation="어머니", addressTerm="엄마")
        _pay(client, h, grandma, "memories")
        # 다른 페르소나는 따로: 아직 체험 중
        assert _pv(client, h, mom)["status"] == "TRIAL"
        # 등록된 카드로 결제 창 없이 바로 시작(초기 가족 요금은 가족 단위라 둘째도 초기 요금)
        started = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": mom, "plan": "family", "agreements": AGREE},
        )
        assert started.status_code == 200, started.text
        data = started.json()["data"]
        assert "checkoutUrl" not in data and data["started"] and data["charged"] == 14900
        assert _pv(client, h, mom)["plan"] == "family"
        assert _pv(client, h, grandma)["plan"] == "memories"
        pays = _payments(email)
        assert [(p.persona_id, p.amount) for p in pays] == [(grandma, 4900), (mom, 14900)]
        me = _me(client, h)
        assert {p["personaId"]: p["plan"] for p in me["personas"]} == {
            grandma: "memories",
            mom: "family",
        }
        assert {p["personaId"]: p["title"] for p in me["personas"]} == {
            grandma: "할머니",
            mom: "엄마",
        }

        # 대화 횟수도 페르소나마다 따로
        _use_conversations(email, mom, 10)
        assert _pv(client, h, mom)["conversationsUsed"] == 10
        assert _pv(client, h, grandma)["conversationsUsed"] == 0

        # 페르소나를 지우면 그 구독도 끝난다(더 결제하지 않는다). 결제 기록은 남는다
        assert client.delete(f"{API}/personas/{mom}", headers=h).status_code == 200
        _set_sub(grandma, current_period_end=datetime.now(UTC) - timedelta(minutes=1))
        _renew()
        assert [p.persona_id for p in _payments(email)] == [grandma, mom, grandma]
        history = client.get(f"{API}/billing/payments", headers=h).json()["data"]
        assert [p["personaTitle"] for p in history] == ["할머니", "지운 대화 상대", "할머니"]
        # 남의 페르소나 요금제는 볼 수도 고를 수도 없다
        other = _signup(client, "not-mine@example.com")
        assert client.get(f"{API}/billing/personas/{grandma}", headers=other).status_code == 404
        stolen = client.post(
            f"{API}/billing/checkout",
            headers=other,
            json={"personaId": grandma, "plan": "family", "agreements": AGREE},
        )
        assert stolen.status_code == 404


def test_trial_personas_are_capped_per_account(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """체험은 페르소나마다지만 계정에 상한이 있다. 지우고 다시 만들어도 되살아나지 않는다."""
    monkeypatch.setattr(get_settings(), "trial_personas_per_account", 2)
    with TestClient(app) as client:
        h = _signup(client, "capped@example.com")
        first, second = _bare_ready(client, h), _bare_ready(client, h)
        assert _pv(client, h, first)["status"] == "TRIAL"
        assert _pv(client, h, second)["status"] == "TRIAL"
        assert _me(client, h)["trialsLeft"] == 0
        client.delete(f"{API}/personas/{first}", headers=h)
        third = _bare_ready(client, h)
        pv = _pv(client, h, third)
        assert pv["status"] == "NO_TRIAL" and pv["active"] is False
        cid = client.post(f"{API}/personas/{third}/conversation", headers=h).json()["data"][
            "conversationId"
        ]
        blocked = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "안녕"}
        )
        assert blocked.status_code == 402 and "대화 상대 2명까지" in blocked.json()["detail"]
        # 요금제를 고르면 바로 대화할 수 있다
        _pay(client, h, third, "memories")
        assert _pv(client, h, third)["status"] == "ACTIVE"


def test_checkout_session_is_single_use(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "once@example.com")
        pid = _bare_ready(client, h)
        session, ck, _ = _open(client, h, pid, "memories")
        params = {"session": session, "customerKey": ck, "authKey": "mock_x"}
        first = client.get(f"{API}/billing/callback/success", params=params, follow_redirects=False)
        assert first.headers["location"].endswith("result=success")
        again = client.get(f"{API}/billing/callback/success", params=params, follow_redirects=False)
        assert again.status_code == 400  # 새로고침해도 다시 결제하지 않는다
        assert len(_payments("once@example.com")) == 1
        other = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": pid, "plan": "family", "agreements": AGREE},
        )
        assert other.json()["data"]["changed"] is True  # 이미 카드가 있어 바로 바뀐다


def test_trial_conversation_count_and_expiry(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """대화 = 말하고 대답 듣기 1번. 목소리 듣기는 따로 세지 않는다. 다 쓰면 보내기 전에 멈춘다."""
    email = "quota@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
            "conversationId"
        ]
        monkeypatch.setattr(get_settings(), "trial_conversations", 2)
        assert _pv(client, h, pid)["conversationsLeft"] == 2
        sent = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
        )
        assert sent.status_code == 200 and sent.json()["data"]["conversationsLeft"] == 1
        reply = sent.json()["data"]["assistantMessage"]["id"]
        # 대답 목소리는 몇 번을 들어도 대화 횟수에 들어가지 않는다
        assert client.post(f"{API}/messages/{reply}/speech", headers=h).status_code == 200
        assert client.post(f"{API}/messages/{reply}/speech", headers=h).status_code == 200
        assert _pv(client, h, pid)["conversationsUsed"] == 1
        sent = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "또 왔어요"}
        )
        assert sent.json()["data"]["conversationsLeft"] == 0
        # 다 써도 끊지 않고 글로 대답한다(하드캡 금지). 그 대답은 목소리로 만들지 않는다
        over = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "하나 더"}
        )
        assert over.status_code == 200, over.text
        data = over.json()["data"]
        assert data["textOnly"] is True and data["conversationsLeft"] == 0
        assert data["assistantMessage"]["textOnly"] is True
        spoken = client.post(f"{API}/messages/{data['assistantMessage']['id']}/speech", headers=h)
        assert spoken.status_code == 409 and "글로만" in spoken.json()["detail"]
        # 말로 보내면 글로 옮기기(돈이 든다) 전에 글로 보내 달라고 한다
        voice = client.post(
            f"{API}/conversations/{cid}/messages/voice",
            headers=h,
            files={"file": ("voice.webm", b"fake", "audio/webm")},
        )
        assert voice.status_code == 409 and "글로" in voice.json()["detail"]
        assert "/v1/voice/transcriptions" not in fake_ai.calls
        pv = _pv(client, h, pid)
        assert pv["conversationsUsed"] == 2 and pv["conversationsLeft"] == 0
        assert pv["textOnly"] is True
        # 글 대답에도 한 달 공정 사용 상한이 있다
        monkeypatch.setattr(get_settings(), "text_only_fair_use", 1)
        capped = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "또"}
        )
        assert capped.status_code == 402 and "모두 썼어요" in capped.json()["detail"]

        _set_persona(pid, trial_ends_at=datetime.now(UTC) - timedelta(seconds=1))
        blocked = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "안녕"}
        )
        assert blocked.status_code == 402 and "무료 체험이 끝났어요" in blocked.json()["detail"]
        # 기록은 그대로 볼 수 있다
        assert client.get(f"{API}/conversations/{cid}", headers=h).status_code == 200


def _use_conversations(email: str, pid: str, n: int) -> None:
    async def go():
        async with SessionLocal() as db:
            user = await db.scalar(select(User).where(User.email == email))
            for _ in range(n):
                db.add(UsageEvent(user_id=user.id, persona_id=pid, kind="conversation"))
            await db.commit()

    asyncio.run(go())


def _backdate_conversations(pid: str, by: timedelta) -> None:
    async def go():
        async with SessionLocal() as db:
            for row in (
                await db.scalars(select(UsageEvent).where(UsageEvent.persona_id == pid))
            ).all():
                row.created_at = row.created_at - by
            await db.commit()

    asyncio.run(go())


def test_monthly_conversations_refill_and_upgrade(fake_ai: FakeAI):  # noqa: F811
    """구독은 결제일부터 한 달 대화 횟수. 체험 때 쓴 횟수는 넘어오지 않는다. 올리면 바로 더 된다."""
    email = "monthly@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
            "conversationId"
        ]
        _use_conversations(email, pid, 5)  # 체험 중에 쓴 대화
        assert _pv(client, h, pid)["conversationsUsed"] == 5
        _pay(client, h, pid, "memories")
        pv = _pv(client, h, pid)
        assert (pv["conversationsUsed"], pv["conversationsLeft"]) == (0, 50)
        _use_conversations(email, pid, 50)
        over = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
        )
        assert over.status_code == 200 and over.json()["data"]["textOnly"] is True
        assert _pv(client, h, pid)["conversationsUsed"] == 50  # 글 대답은 횟수에 넣지 않는다
        # Family 로 올리면 같은 달에 300번까지(초기 요금끼리 차액)
        up = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": pid, "plan": "family", "agreements": AGREE},
        )
        assert up.status_code == 200, up.text
        assert 8000 < up.json()["data"]["charged"] <= 14900 - 4900
        pv = _pv(client, h, pid)
        assert pv["plan"] == "family" and pv["conversationsLeft"] == 250
        ok = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
        )
        assert ok.status_code == 200 and ok.json()["data"]["conversationsLeft"] == 249
        # 다음 결제일이 지나 갱신되면 다시 채워진다(지난 대화는 지난 기간에 한 것으로)
        _backdate_conversations(pid, timedelta(days=1))
        _set_sub(pid, current_period_end=datetime.now(UTC) - timedelta(minutes=1))
        _renew()
        pv = _pv(client, h, pid)
        assert pv["conversationsUsed"] == 0 and pv["conversationsLeft"] == 300
        assert [p.amount for p in _payments(email)][-1] == 14900


def test_beta_price_ends_and_closes(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """초기 가족 요금은 끝나면 동의를 받고 정식 요금으로, 자리가 다 차면 새 가족은 정식 요금으로."""
    with TestClient(app) as client:
        early = "early@example.com"
        h = _signup(client, early)
        pid = _bare_ready(client, h)
        _pay(client, h, pid, "memories")
        assert _payments(early)[-1].amount == 4900
        # 30일 안에 끝나면 요금제 화면에서 정식 요금 동의를 묻는다
        _set_user(early, beta_until=datetime.now(UTC) + timedelta(days=10))
        assert _me(client, h)["regularPriceConsentNeeded"] is True
        # 동의하지 않으면 오른 값으로 결제하지 않고 끝낸다(정기결제 가격 인상은 사전 동의)
        _set_user(early, beta_until=datetime.now(UTC) - timedelta(days=1))
        _set_persona(pid, trial_ends_at=datetime.now(UTC) - timedelta(days=1))
        _set_sub(pid, current_period_end=datetime.now(UTC) - timedelta(minutes=1))
        _renew()
        assert _payments(early)[-1].amount == 4900 and len(_payments(early)) == 1
        assert _pv(client, h, pid)["status"] == "EXPIRED"
        # 동의하면 정식 요금으로 다시 시작할 수 있다(등록된 카드는 끝난 구독 것이라 결제 창)
        assert client.post(f"{API}/billing/regular-price-consent", headers=h).status_code == 200
        assert _me(client, h)["regularPriceConsentNeeded"] is False
        _pay(client, h, pid, "memories")
        assert _payments(early)[-1].amount == 9900
        _set_sub(pid, current_period_end=datetime.now(UTC) - timedelta(minutes=1))
        _renew()
        assert _payments(early)[-1].amount == 9900
        plans = {p["id"]: p for p in _me(client, h)["plans"]}
        assert plans["memories"]["chargePrice"] == 9900 and not plans["memories"]["beta"]

        monkeypatch.setattr(get_settings(), "beta_families", 0)  # 자리가 다 찼다
        late = "late-family@example.com"
        h2 = _signup(client, late)
        pid2 = _bare_ready(client, h2)
        plans = {p["id"]: p for p in _me(client, h2)["plans"]}
        assert plans["family"]["chargePrice"] == 29900 and not plans["family"]["beta"]
        page_url = client.post(
            f"{API}/billing/checkout",
            headers=h2,
            json={"personaId": pid2, "plan": "family", "agreements": AGREE},
        ).json()["data"]["checkoutUrl"]
        page = client.get(page_url.replace("http://localhost:8000", "")).text
        assert "29,900원" in page and "초기 가족 요금" not in page
        _pay(client, h2, pid2, "family")
        assert _payments(late)[-1].amount == 29900
        assert _me(client, h2)["betaUntil"] is None


def _ready_persona(client: TestClient, h: dict) -> str:
    pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
    client.post(
        f"{API}/personas/{pid}/recordings",
        headers=h,
        data={"pledge": "true"},
        files=[("files", ("통화01.wav", _wav(), "audio/wav"))],
    )
    client.post(f"{API}/personas/{pid}/transcribe", headers=h)
    _wait(client, h, pid, "TRANSCRIBE")
    client.post(f"{API}/personas/{pid}/speakers", headers=h, json={"voiceKeys": ["VOICE_1"]})
    _wait(client, h, pid, "ANALYZE")
    # 돌아가신 분은 만들기 전에 신청 자격·유족 명단을 받는다(2026-10-22 법률 자문)
    declare_bereaved(client, pid)
    built = client.post(
        f"{API}/personas/{pid}/build",
        headers=h,
        json={"answers": ANSWERS, "cloneVoice": True, "voiceConsent": True},
    )
    assert built.status_code == 200, built.text
    _wait(client, h, pid, "BUILD")
    return pid


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
def test_adding_recordings_to_a_ready_persona(fake_ai: FakeAI):  # noqa: F811
    """다 만든 페르소나에 녹음을 더 올리고 반영한다(대화는 그동안에도 된다). 새 녹음만 옮기고
    분석하며, 같은 그분 목소리를 이어 받는다. 목소리는 다시 만들지 않는다."""
    with TestClient(app) as client:
        h = _signup(client, "more-recordings@example.com")
        pid = _ready_persona(client, h)
        added = client.post(
            f"{API}/personas/{pid}/recordings",
            headers=h,
            data={"pledge": "true"},
            files=[("files", ("통화02.wav", _wav(), "audio/wav"))],
        )
        assert added.status_code == 200, added.text
        assert client.post(f"{API}/personas/{pid}/recordings/apply", headers=h).status_code == 200
        _wait(client, h, pid, "EXTEND")
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        assert detail["step"] == "READY"
        assert [r["status"] for r in detail["recordings"]] == ["TRANSCRIBED", "TRANSCRIBED"]
        assert len(fake_ai.calls["/v1/analysis/transcriptions"]) == 2  # 새 녹음만 옮김
        assert len(fake_ai.calls["/v1/analysis/recording"]) == 2  # 새 녹음만 분석
        assert len(fake_ai.calls["/v1/persona/assembly"]) == 2  # 지침을 다시 만듦
        assert len(fake_ai.calls["/v1/voice/clone"]) == 1
        nothing = client.post(f"{API}/personas/{pid}/recordings/apply", headers=h)
        assert nothing.status_code == 400


def test_toss_provider_requests(monkeypatch):
    """토스 자동결제: 빌링키 발급·결제 요청 모양(실제 호출 없이)."""
    seen = []

    async def fake_post(self, path, body, idempotency_key=None):
        seen.append((path, body, self._auth()["Authorization"], idempotency_key))
        if path.endswith("/issue"):
            return {"billingKey": "bk_1", "cardCompany": "신한", "card": {"number": "1234****"}}
        return {
            "status": "DONE",
            "paymentKey": "pk_1",
            "approvedAt": "2026-10-13T10:00:00+09:00",
            "receipt": {"url": "https://receipt"},
        }

    monkeypatch.setattr(billing.TossProvider, "_post", fake_post)
    toss = billing.TossProvider("test_ck_x", "test_sk_y")
    assert toss.test_mode
    key, label = asyncio.run(toss.issue_billing_key(auth_key="ak", customer_key="ck"))
    assert key == "bk_1" and label == "신한 1234****"
    result = asyncio.run(
        toss.charge(
            billing_key="bk_1",
            customer_key="ck",
            amount=9900,
            order_id="o1",
            order_name="TalkTo Memories 1개월",
            customer_name="홍길동",
            customer_email=None,
        )
    )
    assert result.ok and result.payment_key == "pk_1"
    assert seen[0][0] == "/v1/billing/authorizations/issue"
    assert seen[1][0] == "/v1/billing/bk_1" and seen[1][1]["amount"] == 9900
    assert seen[1][3] == "o1"  # 같은 주문은 한 번만 결제되게
    import base64

    assert seen[0][2] == "Basic " + base64.b64encode(b"test_sk_y:").decode()
    html = toss.checkout_html(
        customer_key="ck",
        plan=billing.plans()["memories"],
        amount=4900,
        beta=True,
        success_url="https://s?session=1",
        fail_url="https://f?session=1",
        customer_name="홍길동",
        customer_email=None,
    )
    assert "requestBillingAuth" in html and "test_ck_x" in html and "test_sk_y" not in html
    assert "월 4,900원" in html and "원래 가격은 월 9,900원" in html


def test_add_month_keeps_day():
    assert billing.add_month(datetime(2026, 1, 31, tzinfo=UTC)).day == 28
    assert billing.add_month(datetime(2026, 12, 15, tzinfo=UTC)) == datetime(
        2027, 1, 15, tzinfo=UTC
    )


def test_two_open_checkout_windows_charge_once(fake_ai: FakeAI):  # noqa: F811
    """같은 페르소나로 결제 창 두 개를 열어 두고 둘 다 끝내도 한 달 값은 한 번만 받는다."""
    email = "twowin@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        a = _open(client, h, pid, "memories")
        b = _open(client, h, pid, "family")
        ok = client.get(
            f"{API}/billing/callback/success",
            params={"session": b[0], "customerKey": b[1], "authKey": b[2]},
            follow_redirects=False,
        )
        assert ok.headers["location"].endswith("result=success")
        late = client.get(
            f"{API}/billing/callback/success",
            params={"session": a[0], "customerKey": a[1], "authKey": a[2]},
            follow_redirects=False,
        )
        assert late.status_code == 400  # 다른 창은 첫 결제 때 닫혔다
        assert [(p.plan, p.amount) for p in _payments(email)] == [("family", 14900)]
        assert _pv(client, h, pid)["plan"] == "family"


def test_unknown_charge_is_settled_not_charged_twice(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """연결이 끊겨 결과를 모르는 결제는 실패로 치지 않고, 같은 주문 번호로 물어 정한다."""
    email = "unknown@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        orders = []

        async def pending_charge(self, **kw):
            orders.append(kw["order_id"])
            return billing.ChargeResult(False, pending=True, failure="결과를 확인하고 있어요.")

        monkeypatch.setattr(billing.MockProvider, "charge", pending_charge)
        location = _pay(client, h, pid, "memories")
        assert "결제 결과를 확인하고 있어요" in unquote(location)
        assert [p.status for p in _payments(email)] == ["UNKNOWN"]
        assert _pv(client, h, pid)["status"] == "TRIAL"
        # 결과를 모르는 동안에는 새 결제를 받지 않는다
        again = _pay(client, h, pid, "memories")
        assert "방금 하신 결제가 잘 됐는지 확인하고 있어요" in unquote(again)
        assert len(orders) == 1

        async def found(self, order_id):
            assert order_id == orders[0]  # 같은 주문 번호로 묻는다
            return billing.ChargeResult(True, payment_key="pk", approved_at=billing.now())

        monkeypatch.setattr(billing.MockProvider, "lookup", found)

        async def settle():
            async with SessionLocal() as db:
                user = await db.scalar(select(User).where(User.email == email))
                pay = await db.scalar(select(Payment).where(Payment.user_id == user.id))
                pay.created_at = datetime.now(UTC) - timedelta(minutes=5)  # 2분 넘게 지난 것처럼
                await db.commit()
                await billing.settle_unknown(db)

        asyncio.run(settle())
        assert [p.status for p in _payments(email)] == ["DONE"]
        pv = _pv(client, h, pid)
        assert pv["plan"] == "memories" and pv["status"] == "ACTIVE"


def test_toss_retries_same_order_then_looks_it_up(monkeypatch):
    calls = []

    async def flaky(self, method, path, body=None, *, idempotency_key=None):
        calls.append((method, path, idempotency_key))
        if method == "POST":
            raise billing.BillingUnknown("timeout")
        return {"status": "DONE", "paymentKey": "pk", "approvedAt": "2026-10-13T10:00:00+09:00"}

    monkeypatch.setattr(billing.TossProvider, "_request", flaky)
    toss = billing.TossProvider("test_ck_x", "test_sk_y")
    result = asyncio.run(
        toss.charge(
            billing_key="bk",
            customer_key="ck",
            amount=9900,
            order_id="o1",
            order_name="x",
            customer_name="홍길동",
            customer_email=None,
        )
    )
    assert result.ok and result.payment_key == "pk"
    assert calls == [
        ("POST", "/v1/billing/bk", "o1"),
        ("POST", "/v1/billing/bk", "o1"),  # 같은 주문·같은 열쇠로 한 번 더
        ("GET", "/v1/payments/orders/o1", None),
    ]


def test_checkout_page_escapes_names_and_no_free_mock_on_real_server(monkeypatch):
    toss = billing.TossProvider("test_ck_x", "test_sk_y")
    html = toss.checkout_html(
        customer_key="ck",
        plan=billing.plans()["memories"],
        amount=9900,
        beta=False,
        success_url="https://s?session=1",
        fail_url="https://f?session=1",
        customer_name="</script><script>alert(1)</script>",
        customer_email=None,
    )
    assert "</script><script>alert(1)" not in html
    assert "\\u003c/script\\u003e" in html
    # 키 없이 배포한 서버에서는 시험 결제(무료로 구독이 켜짐)를 열지 않는다
    monkeypatch.setattr(get_settings(), "environment", "development")
    monkeypatch.setattr(get_settings(), "public_base_url", "https://api.talkto.kr")
    assert billing.provider() is None
    monkeypatch.setattr(get_settings(), "public_base_url", "http://localhost:8000")
    assert isinstance(billing.provider(), billing.MockProvider)


def test_billing_day_survives_short_months():
    start = datetime(2026, 1, 30, 16, 0, tzinfo=UTC)  # 한국 1월 31일 새벽 1시
    day = billing.kst_day(start)
    assert day == 31
    feb = billing.add_month(start, day)
    mar = billing.add_month(feb, day)
    assert feb.astimezone(billing.KST).day == 28
    assert mar.astimezone(billing.KST).day == 31  # 짧은 달 뒤에도 31일로 돌아온다


def test_late_renewal_charges_one_month_from_now(fake_ai: FakeAI):  # noqa: F811
    """서버가 두 달 멈췄다 켜져도 지난 달들을 몰아서 결제하지 않는다."""
    email = "late@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        _pay(client, h, pid, "memories")
        _set_sub(pid, current_period_end=datetime.now(UTC) - timedelta(days=60))
        _renew()
        _renew()
        assert [p.amount for p in _payments(email)] == [4900, 4900]
        end = datetime.fromisoformat(_pv(client, h, pid)["periodEnd"])
        assert end - datetime.now(UTC) > timedelta(days=27)


def test_past_due_goes_back_to_card_window(fake_ai: FakeAI):  # noqa: F811
    email = "pastdue@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        _pay(client, h, pid, "memories")
        _set_sub(
            pid,
            status="PAST_DUE",
            current_period_end=datetime.now(UTC) - timedelta(days=1),
        )
        started = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": pid, "plan": "family", "agreements": AGREE},
        )
        # 밀린 구독은 등록된 카드로 100원 차액을 받지 않고, 카드를 다시 받는다
        assert "checkoutUrl" in started.json()["data"]
        # 밀린 카드는 다른 페르소나에도 쓰지 않는다
        other = _bare_ready(client, h)
        fresh = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": other, "plan": "memories", "agreements": AGREE},
        )
        assert "checkoutUrl" in fresh.json()["data"]


def _wait_job_end(client: TestClient, h: dict, pid: str, kind: str, timeout: float = 20) -> None:
    import time

    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["jobs"].get(kind)
        if job and job["status"] != "RUNNING":
            return
        time.sleep(0.1)
    raise AssertionError(f"{kind} did not finish")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
def test_artifacts_are_admin_only_and_delete_removes_the_vendor_voice(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "artifacts@example.com")
        pid = _ready_persona(client, h)
        # 지침은 고객에게 보이지 않는다(관리자만 본다)
        assert client.get(f"{API}/personas/{pid}/artifacts", headers=h).status_code == 404
        assert client.get(f"{API}/admin/personas/{pid}/artifacts", headers=h).status_code == 403
        # 관리자의 지침 보기에도 성함 대신 호칭
        admin = _admin(client)
        art = client.get(f"{API}/admin/personas/{pid}/artifacts", headers=admin).json()["data"]
        assert art["instructions"] and art["memories"]
        assert BASICS["name"] not in art["instructions"]
        assert all(BASICS["name"] not in m["text"] for m in art["memories"])
        # 페르소나를 지우면 목소리를 만든 회사(Fish)에서도 지운다
        assert client.delete(f"{API}/personas/{pid}", headers=h).status_code == 200
        assert fake_ai.calls["/v1/voice/delete"][-1] == {
            "voiceId": fake_ai.voice(1),
            "provider": "fish",
        }


def test_premium_plan_and_its_routes_are_gone(fake_ai: FakeAI):  # noqa: F811
    """똑 닮은 목소리 요금제와 그 화면 API 는 없다(랜딩 페이지도 /billing/plans 를 읽는다)."""
    with TestClient(app) as client:
        plans = client.get(f"{API}/billing/plans").json()["data"]
        assert [p["id"] for p in plans] == ["trial", "memories", "family"]
        h = _signup(client, "no-premium@example.com")
        pid = _bare_ready(client, h)
        assert client.get(f"{API}/personas/{pid}/premium-voice", headers=h).status_code == 404
        assert client.get(f"{API}/premium-voice/consent-form").status_code == 404
        assert "pvcMinSeconds" not in _me(client, h)
        consents = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["consents"]
        assert "pvcTransfer" not in consents


def test_settling_an_old_premium_charge_never_brings_the_plan_back(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """없앤 요금제로 결과를 모르던 결제가 나중에 결제됐다고 나와도 구독에는 가족 요금제로 적는다."""
    email = "late-premium@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        _pay(client, h, pid, "memories")

        async def add_unknown():
            async with SessionLocal() as db:
                user = await db.scalar(select(User).where(User.email == email))
                db.add(
                    Payment(
                        user_id=user.id,
                        persona_id=pid,
                        plan="premium",
                        provider="mock",
                        order_id="talkto_old_premium",
                        amount=54900,
                        kind="upgrade",
                        status="UNKNOWN",
                    )
                )
                await db.commit()

        asyncio.run(add_unknown())

        async def done(self, order_id):
            return billing.ChargeResult(True, payment_key="pk", approved_at=billing.now())

        monkeypatch.setattr(billing.MockProvider, "lookup", done)
        _settle(email)
        pv = _pv(client, h, pid)
        assert pv["plan"] == "family" and pv["status"] == "ACTIVE"
        history = client.get(f"{API}/billing/payments", headers=h).json()["data"]
        # 결제 기록은 그때 낸 그대로(없앤 요금제 이름으로) 남긴다
        names = {p["plan"]: p["planName"] for p in history}
        assert names == {"memories": "추억", "premium": "똑 닮은 목소리(종료)"}


def test_shown_beta_price_is_what_gets_charged(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """초기 요금이 없는 요금제로 시작한 가족이 내려도, 보인 초기 요금 그대로 갱신 결제한다."""
    email = "regular-first@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        monkeypatch.setattr(get_settings(), "plan_family_beta_price", None)  # 정식 요금만
        _pay(client, h, pid, "family")
        assert _payments(email)[-1].amount == 29900 and _me(client, h)["betaUntil"] is None
        shown = {p["id"]: p for p in _me(client, h)["plans"]}["memories"]["chargePrice"]
        assert shown == 4900
        down = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": pid, "plan": "memories", "agreements": AGREE},
        )
        assert down.json()["data"]["scheduled"] == "memories"
        _set_sub(pid, current_period_end=datetime.now(UTC) - timedelta(minutes=1))
        _renew()
        assert _payments(email)[-1].amount == shown


def test_failed_upgrade_keeps_cancellation(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """해지 예약 뒤 올리기 결제가 실패하면 해지 예약은 그대로(몰래 다시 결제되지 않게)."""
    with TestClient(app) as client:
        h = _signup(client, "cancel-then-upgrade@example.com")
        pid = _bare_ready(client, h)
        _pay(client, h, pid, "memories")
        client.post(f"{API}/billing/cancel", headers=h, json={"personaId": pid})

        async def declined(self, **kw):
            return billing.ChargeResult(False, failure="카드가 거절됐어요.")

        monkeypatch.setattr(billing.MockProvider, "charge", declined)
        up = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": pid, "plan": "family", "agreements": AGREE},
        )
        assert up.status_code == 402
        pv = _pv(client, h, pid)
        assert pv["status"] == "CANCELING" and pv["plan"] == "memories"


def test_failed_reply_and_silent_voice_do_not_use_a_conversation(fake_ai: FakeAI):  # noqa: F811
    from app.services import ai as ai_module

    with TestClient(app, raise_server_exceptions=False) as client:
        h = _signup(client, "no-charge-on-fail@example.com")
        pid = _bare_ready(client, h)
        cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
            "conversationId"
        ]
        # 대답을 받지 못하면 세지 않는다(대화는 AIService.reply 로 대답을 받는다)
        real_reply = ai_module.AIService.reply

        async def broken(self, **kw):
            raise ai_module.AIServerError(503)

        ai_module.AIService.reply = broken
        try:
            failed = client.post(
                f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "안녕"}
            )
        finally:
            ai_module.AIService.reply = real_reply
        assert failed.status_code >= 400
        assert _pv(client, h, pid)["conversationsUsed"] == 0
        # 알아듣지 못한 녹음도 세지 않는다
        real_stt = ai_module.AIService.transcribe

        async def silent(self, *a, **kw):
            return ""

        ai_module.AIService.transcribe = silent
        try:
            voice = client.post(
                f"{API}/conversations/{cid}/messages/voice",
                headers=h,
                files={"file": ("voice.webm", b"fake", "audio/webm")},
            )
        finally:
            ai_module.AIService.transcribe = real_stt
        assert voice.status_code == 422 and "알아듣지 못했어요" in voice.json()["detail"]
        assert _pv(client, h, pid)["conversationsUsed"] == 0
        ok = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
        )
        assert ok.status_code == 200 and _pv(client, h, pid)["conversationsUsed"] == 1


def test_reservation_never_exceeds_limit():
    """동시에 보내도 잡은 순서가 횟수 안에 드는 것만 받는다."""

    async def go():
        async with SessionLocal() as db:
            user = User(email="race@example.com", name="홍길동", password_hash="x")
            db.add(user)
            await db.flush()
            from app.db.models import Person

            person = Person(owner_user_id=user.id, name="김순자", relationship_label="할머니")
            db.add(person)
            await db.flush()
            persona = Persona(person_id=person.id, name="김순자", setup_step=SetupStep.READY)
            db.add(persona)
            await db.commit()
            ent = await billing.entitlement(db, user, persona)
            assert ent.conversations_limit == 30
            ent.conversations_limit = 2
            got = [await billing.reserve_conversation(db, user, persona, ent) for _ in range(4)]
            return got

    got = asyncio.run(go())
    assert [g is not None for g in got] == [True, True, False, False]


def _settle(email: str) -> None:
    async def go():
        async with SessionLocal() as db:
            user = await db.scalar(select(User).where(User.email == email))
            for pay in (
                await db.scalars(
                    select(Payment).where(Payment.user_id == user.id, Payment.status == "UNKNOWN")
                )
            ).all():
                pay.created_at = datetime.now(UTC) - timedelta(minutes=5)
            await db.commit()
            await billing.settle_unknown(db)

    asyncio.run(go())


def test_unknown_upgrade_blocks_second_charge_and_deletion(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """올리기 결과를 모르는 동안에는 다시 올리기·페르소나 지우기를 막는다(두 번 결제 막기)."""
    email = "pending-upgrade@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        _pay(client, h, pid, "memories")

        async def pending(self, **kw):
            return billing.ChargeResult(False, pending=True, failure="결과를 확인하고 있어요.")

        monkeypatch.setattr(billing.MockProvider, "charge", pending)
        body = {"personaId": pid, "plan": "family", "agreements": AGREE}
        first = client.post(f"{API}/billing/checkout", headers=h, json=body)
        assert first.status_code == 402
        assert _pv(client, h, pid)["paymentPending"] is True
        again = client.post(f"{API}/billing/checkout", headers=h, json=body)
        assert again.status_code == 402 and "방금 하신 결제" in again.json()["detail"]
        assert [p.kind for p in _payments(email)] == ["first", "upgrade"]  # 두 번째는 보내지 않음
        deleted = client.delete(f"{API}/personas/{pid}", headers=h)
        assert deleted.status_code == 409 and "결제 결과" in deleted.json()["detail"]


def test_payment_is_recorded_before_the_card_is_charged(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """결제 회사 호출 중에 서버가 멈춰도 결제 기록(결과 모름)이 남아 나중에 정한다."""
    email = "crash-mid-charge@example.com"
    with TestClient(app, raise_server_exceptions=False) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        session, ck, auth = _open(client, h, pid, "memories")

        async def crash(self, **kw):
            raise RuntimeError("process killed")

        monkeypatch.setattr(billing.MockProvider, "charge", crash)
        client.get(
            f"{API}/billing/callback/success",
            params={"session": session, "customerKey": ck, "authKey": auth},
            follow_redirects=False,
        )
        assert [(p.status, p.persona_id) for p in _payments(email)] == [("UNKNOWN", pid)]


def test_settling_keeps_a_cancellation_made_while_waiting(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    email = "cancel-while-unknown@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        _pay(client, h, pid, "memories")
        _set_sub(pid, current_period_end=datetime.now(UTC) - timedelta(minutes=1))

        async def pending(self, **kw):
            return billing.ChargeResult(False, pending=True, failure="결과를 확인하고 있어요.")

        monkeypatch.setattr(billing.MockProvider, "charge", pending)
        _renew()
        # 결과를 기다리는 동안에도 쓸 수 있고(갱신 유예), 해지할 수 있다
        assert _pv(client, h, pid)["active"] is True
        assert (
            client.post(f"{API}/billing/cancel", headers=h, json={"personaId": pid}).status_code
            == 200
        )

        async def done(self, order_id):
            return billing.ChargeResult(True, payment_key="pk", approved_at=billing.now())

        monkeypatch.setattr(billing.MockProvider, "lookup", done)
        _settle(email)
        pv = _pv(client, h, pid)
        assert pv["status"] == "CANCELING" and pv["subscription"]["cancelAtPeriodEnd"] is True


def test_subscribing_after_beta_ended_counts_as_regular_price_consent(fake_ai: FakeAI):  # noqa: F811
    """초기 요금이 끝난 가족이 정식 요금을 보고 새로 구독하면, 갱신 때 끊지 않는다."""
    email = "after-beta@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        first = _bare_ready(client, h)
        _pay(client, h, first, "memories")
        _set_user(email, beta_until=datetime.now(UTC) - timedelta(days=1))
        second = _bare_ready(client, h, relation="어머니", addressTerm="엄마")
        started = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={
                "personaId": second,
                "plan": "memories",
                "agreements": AGREE,
                "mode": "saved_card",
                "expectedAmount": 9900,
            },
        )
        assert started.status_code == 200 and started.json()["data"]["charged"] == 9900
        _set_sub(second, current_period_end=datetime.now(UTC) - timedelta(minutes=1))
        _renew()
        assert _payments(email)[-1].amount == 9900 and _pv(client, h, second)["status"] == "ACTIVE"


def test_saved_card_never_charges_more_than_shown(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """보여 준 값보다 많이 받게 되면(초기 요금 자리가 그사이 다 참) 결제하지 않고 다시 보여 준다."""
    email = "shown-price@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        first = _bare_ready(client, h)
        # 시작은 정식 요금(초기 요금 자리가 없는 가족)
        monkeypatch.setattr(get_settings(), "beta_families", 0)
        _pay(client, h, first, "memories")
        second = _bare_ready(client, h, relation="어머니", addressTerm="엄마")
        # 화면은 4,900원을 보여 줬는데 지금은 9,900원이다
        refused = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={
                "personaId": second,
                "plan": "memories",
                "agreements": AGREE,
                "mode": "saved_card",
                "expectedAmount": 4900,
            },
        )
        assert refused.status_code == 409 and "요금이 바뀌었어요" in refused.json()["detail"]
        # '결제 창에서 카드를 등록해요'라고 안내했으면 등록된 카드로 몰래 결제하지 않는다
        window = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": second, "plan": "memories", "agreements": AGREE, "mode": "window"},
        )
        assert "checkoutUrl" in window.json()["data"]
        assert len(_payments(email)) == 1
        # 이미 구독 중인 페르소나에 '새로 시작'으로 보냈으면 다시 보여 준다
        stale = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": first, "plan": "family", "agreements": AGREE, "mode": "saved_card"},
        )
        assert stale.status_code == 409
        # 올리기 차액은 화면에 미리 보여 준다
        quote = _pv(client, h, first)["upgradeQuotes"]
        assert set(quote) == {"family"} and 0 < quote["family"] <= 29900 - 9900
        up = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={
                "personaId": first,
                "plan": "family",
                "agreements": AGREE,
                "mode": "change",
                "expectedAmount": quote["family"],
            },
        )
        assert up.status_code == 200 and up.json()["data"]["charged"] <= quote["family"]


def test_trial_cap_holds_with_stale_user(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """두 요청이 같은 순간의 사용자 정보로 체험을 열어도 계정 상한을 넘지 않는다."""
    monkeypatch.setattr(get_settings(), "trial_personas_per_account", 1)
    with TestClient(app) as client:
        h = _signup(client, "stale-trial@example.com")
        a, b = _bare_ready(client, h), _bare_ready(client, h)

    async def go():
        async with SessionLocal() as one, SessionLocal() as two:
            u1 = await one.scalar(select(User).where(User.email == "stale-trial@example.com"))
            u2 = await two.scalar(select(User).where(User.email == "stale-trial@example.com"))
            p1, p2 = await one.get(Persona, a), await two.get(Persona, b)
            return await billing._start_trial(one, u1, p1), await billing._start_trial(two, u2, p2)

    assert asyncio.run(go()) == (True, False)


def test_no_chat_before_ready_and_trial_preview(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """만드는 중인 페르소나로는 대화하지 않는다. 만드는 중이면 체험을 받을지 미리 알려 준다."""
    monkeypatch.setattr(get_settings(), "trial_personas_per_account", 1)
    with TestClient(app) as client:
        h = _signup(client, "draft-chat@example.com")
        first = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        second = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        made = client.post(
            f"{API}/conversations", headers=h, json={"personaId": first, "channel": "TEXT"}
        )
        assert made.status_code == 409
        me = _me(client, h)
        will = {p["personaId"]: p["trialWillStart"] for p in me["personas"]}
        assert will == {first: True, second: False}  # 먼저 만든 페르소나가 남은 자리를 받는다
        assert me["trialsAvailableForNew"] == 0
        # 첫 결제가 실패해 남은 빈 구독 줄은 '구독이 끝났어요'가 아니다
        _set_persona(first, setup_step=SetupStep.READY)
        assert _pv(client, h, first)["hadPaidPlan"] is False


def test_trial_preview_counts_ready_personas_first(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """다 만든 페르소나가 먼저 체험을 쓰고, 남은 자리를 만드는 중인 페르소나가 나눠 받는다."""
    monkeypatch.setattr(get_settings(), "trial_personas_per_account", 2)
    with TestClient(app) as client:
        h = _signup(client, "ready-first@example.com")
        ready = _bare_ready(client, h)
        draft = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        me = _me(client, h)
        views = {p["personaId"]: p for p in me["personas"]}
        assert views[ready]["status"] == "TRIAL"
        assert views[draft]["trialWillStart"] is True
        assert me["trialsLeft"] == 1 and me["trialsAvailableForNew"] == 0


def test_regular_price_consent_only_from_a_paid_beta_plan(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """초기 요금이 끝난 가족의 정식 요금 동의는, 초기 요금이 있는 요금제를 실제로 결제했을 때만
    남는다. 거절된 올리기나 초기 요금이 없는(한 가지 값) 요금제 결제로는 다른 페르소나 값이 오르지
    않는다."""
    email = "consent-when-paid@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        grandma = _bare_ready(client, h)
        grandpa = _bare_ready(client, h, relation="할아버지", addressTerm="할아버지")
        _pay(client, h, grandma, "memories")
        assert _payments(email)[-1].amount == 4900
        assert _pv(client, h, grandma)["canChange"] is True
        assert _pv(client, h, grandpa)["canChange"] is False
        _set_user(email, beta_until=datetime.now(UTC) - timedelta(days=1))
        assert _me(client, h)["regularPriceConsentNeeded"] is True

        # 가족 요금제가 한 가지 값(초기 요금 없음)이던 때의 결제
        monkeypatch.setattr(get_settings(), "plan_family_beta_price", None)
        single = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": grandpa, "plan": "family", "agreements": AGREE},
        )
        assert single.status_code == 200, single.text
        assert _payments(email)[-1].amount == 29900
        assert _me(client, h)["regularPriceConsentNeeded"] is True
        monkeypatch.setattr(get_settings(), "plan_family_beta_price", 14900)

        async def declined(self, **kw):
            return billing.ChargeResult(False, failure="카드가 거절됐어요.")

        real_charge = billing.MockProvider.charge
        monkeypatch.setattr(billing.MockProvider, "charge", declined)
        up = client.post(
            f"{API}/billing/checkout",
            headers=h,
            json={"personaId": grandma, "plan": "family", "agreements": AGREE},
        )
        assert up.status_code == 402
        assert _me(client, h)["regularPriceConsentNeeded"] is True
        # 동의가 없으니 갱신 때 오른 값으로 결제하지 않고 끝낸다
        monkeypatch.setattr(billing.MockProvider, "charge", real_charge)
        paid = len(_payments(email))
        _set_persona(grandma, trial_ends_at=datetime.now(UTC) - timedelta(days=1))
        _set_sub(grandma, current_period_end=datetime.now(UTC) - timedelta(minutes=1))
        _renew()
        assert _pv(client, h, grandma)["status"] == "EXPIRED"
        assert [p.amount for p in _payments(email)[paid:] if p.persona_id == grandma] == []
