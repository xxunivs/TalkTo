"""디스코드 알림의 가린 이메일(al***@gmail.com): 새 가입·구독 신청·인터뷰 신청에만 싣고,
통계(analytics_events)·로그에는 전체 이메일도 가린 이메일도 남기지 않는다(네트워크는 안 쓴다)."""

import asyncio
import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_billing import _bare_ready
from test_interview import _apply, _persona
from test_notify import _on, _settle, sent  # noqa: F401
from test_oauth import (  # noqa: F401
    FakeProvider,
    _exchange,
    _me,
    _profile,
    _social_login,
    _ticket,
    fake,
    oauth_settings,
)
from test_persona_setup import AGREEMENTS, FakeAI, fake_ai  # noqa: F401

from app.core.config import get_settings
from app.db.models import SOCIAL_PLACEHOLDER_EMAIL_SUFFIX, AnalyticsEvent
from app.db.session import SessionLocal
from app.main import app
from app.services import notify

API = "/api/v1"


def _events(user_id: str) -> list[tuple[str, dict]]:
    async def go():
        async with SessionLocal() as db:
            rows = await db.scalars(
                select(AnalyticsEvent)
                .where(AnalyticsEvent.user_id == user_id)
                .order_by(AnalyticsEvent.created_at)
            )
            return [(e.name, e.props) for e in rows.all()]

    return asyncio.run(go())


@pytest.mark.parametrize(
    ("email", "masked"),
    [
        ("alexamy.lee@gmail.com", "al***@gmail.com"),
        ("Alexamy.Lee@Gmail.COM", "al***@gmail.com"),  # 대문자는 작게
        ("  abc@x.com ", "ab***@x.com"),
        ("ab+tag@x.com", "ab***@x.com"),  # +태그는 보이지 않는다
        ("a+tag@x.com", "a+***@x.com"),
        ("ab@x.com", "a***@x.com"),  # 1~2글자면 한 글자만
        ("a@x.com", "a***@x.com"),
        ("kim@mail.co.kr", "ki***@mail.co.kr"),
        (f"kakao_123{SOCIAL_PLACEHOLDER_EMAIL_SUFFIX}", "이메일 없음"),  # 이메일 없이 가입한 소셜
        (f"GOOGLE_9{SOCIAL_PLACEHOLDER_EMAIL_SUFFIX.upper()}", "이메일 없음"),
        (None, "이메일 없음"),
        ("", "이메일 없음"),
        ("no-at-sign", "이메일 없음"),
        ("@x.com", "이메일 없음"),
        ("ab@", "이메일 없음"),
    ],
)
def test_masked_email_keeps_two_letters_and_the_domain(email, masked):
    assert notify.masked_email(email) == masked


def test_only_signup_and_requests_carry_the_masked_email(monkeypatch):
    email = "Alexamy.Lee@Gmail.com"
    text = notify._event_text
    assert text("signup", {"method": "kakao"}, email) == "🎉 새 가입 · 카카오 · al***@gmail.com"
    assert text("signup", {"method": "email"}) == "🎉 새 가입 · 이메일"  # 이메일을 안 주면 그대로
    assert text("subscription_requested", {"plan": "family"}, email) == (
        "📝 구독 신청 · 가족 · al***@gmail.com"
    )
    assert text("interview_requested", {"method": "phone"}, email) == (
        "🎙️ 새 기억 인터뷰 신청 1건 · al***@gmail.com — 관리자 화면에서 확인해 주세요"
    )
    assert text("interview_requested", {"method": "phone", "repeat": True}, email) is None
    # 다른 알림에는 싣지 않는다
    for name, props in (
        ("build_done", {"seconds": 120}),
        ("payment_succeeded", {"plan": "family", "amount": 9900}),
        ("subscription_request_canceled", {"plan": "family"}),
        ("account_deleted", {}),
    ):
        assert "***" not in (text(name, props, email) or "")
    pinged: list[str] = []
    monkeypatch.setattr(notify, "send", lambda message: pinged.append(message))
    notify.event("signup", {"method": "google"}, email)
    assert pinged == ["🎉 새 가입 · 구글 · al***@gmail.com"]


def test_email_signup_and_requests_announce_the_masked_email_but_never_store_it(
    sent,  # noqa: F811
    monkeypatch,
    fake_ai,  # noqa: F811
    caplog,
):
    _on(monkeypatch)
    monkeypatch.setattr(get_settings(), "payments_open", False)
    caplog.set_level(logging.DEBUG)
    email = f"tm{uuid4().hex[:8]}@example.com"
    masked = "tm***@example.com"
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
        uid = client.get(f"{API}/auth/me", headers=h).json()["data"]["id"]
        pid = _bare_ready(client, h)
        ok = client.post(
            f"{API}/billing/requests", headers=h, json={"personaId": pid, "plan": "family"}
        )
        assert ok.status_code == 200, ok.text
        assert _apply(client, h, _persona(uid)).status_code == 201
        _settle()
    texts = [t for _, t in sent]
    assert f"🎉 새 가입 · 이메일 · {masked}" in texts
    assert f"📝 구독 신청 · 가족 · {masked}" in texts
    assert any(t.startswith(f"🎙️ 새 기억 인터뷰 신청 1건 · {masked}") for t in texts)
    assert email not in "\n".join(texts)
    # 통계 줄에는 전체 이메일도 가린 이메일도 없다
    events = _events(uid)
    assert ("signup", {"method": "email"}) in events
    names = {name for name, _ in events}
    assert names >= {"signup", "subscription_requested", "interview_requested"}
    stored = str(events)
    for secret in (email, masked, "***", email.split("@")[0]):
        assert secret not in stored
    # 로그에도 남기지 않는다(가린 이메일은 어느 로그에도, 전체 이메일은 우리 서버 로그에)
    assert masked not in caplog.text
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app"))
    assert email not in ours


def test_social_signup_announces_the_masked_email_or_no_email(
    sent,  # noqa: F811
    monkeypatch,
    fake: FakeProvider,  # noqa: F811
):
    _on(monkeypatch)
    email = f"gs{uuid4().hex[:8]}@example.com"
    with TestClient(app, follow_redirects=False) as client:
        # 카카오가 이메일을 주지 않았다(빈 자리 주소로 가입)
        kakao = _exchange(client, _ticket(_social_login(client, fake, _profile("kakao"))))
        google_profile = _profile("google", email=email, email_verified=True)
        google = _exchange(client, _ticket(_social_login(client, fake, google_profile)))
        _ticket(_social_login(client, fake, google_profile))  # 두 번째는 로그인 — 알리지 않는다
        _settle()
        ids = [_me(client, kakao)["id"], _me(client, google)["id"]]
    texts = [t for _, t in sent]
    assert "🎉 새 가입 · 카카오 · 이메일 없음" in texts
    assert "🎉 새 가입 · 구글 · gs***@example.com" in texts
    assert len([t for t in texts if t.startswith("🎉 새 가입")]) == 2
    joined = "\n".join(texts)
    assert email not in joined and SOCIAL_PLACEHOLDER_EMAIL_SUFFIX not in joined
    stored = str([_events(user_id) for user_id in ids])
    for secret in (email, "gs***", "***", SOCIAL_PLACEHOLDER_EMAIL_SUFFIX):
        assert secret not in stored
