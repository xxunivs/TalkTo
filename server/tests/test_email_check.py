"""이메일 가입: 메일을 받을 수 있는 주소인지(오타·버리는 메일·없는 도메인). DNS 는 가짜로 바꾼다."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services import email_check

API = "/api/v1"


@pytest.fixture(autouse=True)
def fresh_cache():
    email_check._cache.clear()
    yield
    email_check._cache.clear()


def _dns(monkeypatch, answer):
    """도메인별 가짜 DNS 답(True=메일 받음, False=못 받음, None=DNS 문제)."""
    asked: list[str] = []

    async def fake(domain: str):
        asked.append(domain)
        return answer(domain) if callable(answer) else answer

    monkeypatch.setattr(email_check, "_receives_mail", fake)
    return asked


def test_typos_disposable_and_known_domains(monkeypatch):
    asked = _dns(monkeypatch, True)
    hint = asyncio.run(email_check.problem("halmoni@gmial.com"))
    assert hint and "halmoni@gmail.com" in hint
    assert "버리는" in asyncio.run(email_check.problem("x@mailinator.com"))
    assert asyncio.run(email_check.problem("family@naver.com")) is None
    assert asyncio.run(email_check.problem("nobody")) == email_check.NO_MAIL
    assert asked == []  # 큰 메일 회사·오타·버리는 메일은 DNS 를 묻지 않는다


def test_domain_without_mail_is_refused_and_dns_trouble_lets_it_through(monkeypatch):
    asked = _dns(monkeypatch, lambda d: {"real-company.kr": True, "no-such.kr": False}.get(d))
    assert asyncio.run(email_check.problem("a@real-company.kr")) is None
    assert asyncio.run(email_check.problem("a@no-such.kr")) == email_check.NO_MAIL
    # DNS 를 확인하지 못하면(None) 막지 않는다
    assert asyncio.run(email_check.problem("a@flaky.kr")) is None
    # 확인한 도메인은 한동안 다시 묻지 않는다(확인 못 한 것은 다음에 다시 묻는다)
    asyncio.run(email_check.problem("b@no-such.kr"))
    assert asked.count("no-such.kr") == 1


def test_signup_refuses_addresses_that_cannot_receive_mail(monkeypatch):
    monkeypatch.setattr(get_settings(), "email_domain_check", True)
    _dns(monkeypatch, lambda d: d != "fake-domain-xyz.com")
    with TestClient(app) as client:
        bad = client.post(
            f"{API}/auth/signup",
            json={
                "email": f"{uuid4().hex[:8]}@fake-domain-xyz.com",
                "password": "password123",
                "name": "가",
            },
        )
        assert bad.status_code == 400
        assert bad.json()["detail"] == email_check.NO_MAIL
        typo = client.post(
            f"{API}/auth/signup",
            json={"email": "someone@naver.con", "password": "password123", "name": "가"},
        )
        assert typo.status_code == 400 and "someone@naver.com" in typo.json()["detail"]
        ok = client.post(
            f"{API}/auth/signup",
            json={
                "email": f"{uuid4().hex[:8]}@good-domain.co.kr",
                "password": "password123",
                "name": "가",
            },
        )
        assert ok.status_code == 201, ok.text


def test_check_is_off_unless_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "email_domain_check", False)
    asked = _dns(monkeypatch, False)
    with TestClient(app) as client:
        ok = client.post(
            f"{API}/auth/signup",
            json={
                "email": f"{uuid4().hex[:8]}@example.com",
                "password": "password123",
                "name": "가",
            },
        )
        assert ok.status_code == 201, ok.text
    assert asked == []
