"""디스코드 운영 알림: 운영에서만, 개인 정보 없이(이메일은 가려서), 같은 오류는 묶어서."""

import asyncio
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_billing import _admin, _bare_ready
from test_persona_setup import AGREEMENTS, FakeAI, fake_ai  # noqa: F401

from app.core.config import get_settings
from app.main import app
from app.services import notify

API = "/api/v1"
HOOK = "https://discord.com/api/webhooks/123/test-token"


@pytest.fixture
def sent(monkeypatch):
    """보낸 글(채널 주소, 글). 실제로 디스코드에 보내지 않는다."""
    out: list[tuple[str, str]] = []

    async def fake_post(url: str, text: str) -> None:
        out.append((url, text))

    monkeypatch.setattr(notify, "_post", fake_post)
    notify._sent.clear()
    notify._errors.clear()
    yield out
    notify._sent.clear()
    notify._errors.clear()


def _on(monkeypatch, alerts_url: str | None = None):
    settings = get_settings()
    # 운영인 것처럼(서버 설정 검사는 그대로 두고 알림만)
    monkeypatch.setattr(notify, "_production", lambda: True)
    monkeypatch.setattr(settings, "discord_events_webhook_url", HOOK)
    monkeypatch.setattr(settings, "discord_alerts_webhook_url", alerts_url)


def _settle():
    time.sleep(0.2)


def test_nothing_is_sent_outside_production_or_without_a_discord_url(sent, monkeypatch):
    async def go():
        notify.send("시험")
        await asyncio.sleep(0)

    asyncio.run(go())  # 기본(로컬)은 보내지 않는다
    settings = get_settings()
    monkeypatch.setattr(notify, "_production", lambda: True)
    monkeypatch.setattr(settings, "discord_events_webhook_url", "https://example.com/hook")
    asyncio.run(go())  # 디스코드 주소가 아니면 보내지 않는다
    assert sent == []


def test_signup_and_request_are_announced_without_personal_data(
    sent,
    monkeypatch,
    fake_ai,  # noqa: F811
):
    _on(monkeypatch)
    email = f"notify-{uuid4().hex[:6]}@example.com"
    with TestClient(app) as client:
        r = client.post(
            f"{API}/auth/signup",
            json={
                "email": email,
                "password": "password123",
                "name": "홍길동",
                "agreements": AGREEMENTS,
            },
        )
        assert r.status_code == 201, r.text
        h = {"Authorization": f"Bearer {r.json()['data']['bearerToken']}"}
        pid = _bare_ready(client, h)
        ok = client.post(
            f"{API}/billing/requests",
            headers=h,
            json={"personaId": pid, "plan": "family"},
        )
        assert ok.status_code == 200, ok.text
        _settle()
    texts = [t for _, t in sent]
    # 이메일은 앞 두 글자와 도메인만(no***@example.com)
    assert "🎉 새 가입 · 이메일 · no***@example.com" in texts
    assert "📝 구독 신청 · 가족 · no***@example.com" in texts
    joined = "\n".join(texts)
    for secret in (email, "홍길동", "김순자"):
        assert secret not in joined


def test_errors_go_to_the_errors_channel_and_repeat_errors_are_grouped(sent, monkeypatch):
    alerts_hook = "https://discord.com/api/webhooks/456/errors"
    _on(monkeypatch, alerts_hook)

    async def go():
        for _ in range(5):
            notify.error("KeyError", "POST /api/v1/x")
        await asyncio.sleep(0)
        # 10분이 지난 것처럼
        last, more = notify._errors["KeyError"]
        notify._errors["KeyError"] = (last - notify.ERROR_EVERY_SECONDS - 1, more)
        notify.error("KeyError", "POST /api/v1/x")
        await asyncio.sleep(0)

    asyncio.run(go())
    assert [u for u, _ in sent] == [alerts_hook, alerts_hook]
    assert "4번 더" in sent[1][1]


def test_admin_can_send_a_test_message(sent, monkeypatch):
    _on(monkeypatch)
    with TestClient(app) as client:
        r = client.post(f"{API}/admin/notify-test", headers=_admin(client))
        assert r.status_code == 200
        assert r.json()["data"]["configured"] == {"events": True, "alerts": True}
        _settle()
    assert len(sent) == 2 and all(u == HOOK for u, _ in sent)
