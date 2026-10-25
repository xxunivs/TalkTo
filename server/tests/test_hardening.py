"""운영 보안 다지기: 운영 설정 확인·요청 횟수 제한·본문 크기 상한·오류 응답·ffmpeg·작은 버그들."""

import asyncio
import csv
import io
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from starlette.requests import Request
from test_billing import API, _admin
from test_persona_setup import BASICS, _signup

from app import main as main_module
from app.api.v1 import auth as auth_module
from app.api.v1 import conversations, materials, persona_setup, stats
from app.core import body_limit, ratelimit
from app.core.config import Settings, get_settings, unsafe_production_settings
from app.db.models import ErrorLog, Persona, User, UserStatus
from app.db.session import SessionLocal, engine
from app.main import app
from app.services import ai as ai_module
from app.services import audio

MiB = 1024 * 1024


def _email(tag: str) -> str:
    return f"{tag}-{uuid4().hex[:8]}@example.com"


def _db(fn):
    """fn(db) 는 async 함수다. 세션이 닫히기 전에 값을 다 읽어 돌려준다."""

    async def go():
        async with SessionLocal() as db:
            return await fn(db)

    return asyncio.run(go())


def _status(email: str) -> str:
    async def go(db):
        return await db.scalar(select(User.status).where(User.email == email))

    return _db(go)


@pytest.fixture
def limits_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)


# ────────────────────────── 운영 설정 ──────────────────────────

SAFE = {
    "environment": "production",
    "jwt_secret": "s" * 43,
    "bootstrap_admin_password": "p" * 43,
    "cors_allow_localhost": False,
    "ai_server_token": "token",
    "storage_backend": "s3",
}


def test_production_settings_pass_when_deploy_values_are_used():
    assert unsafe_production_settings(Settings(_env_file=None, **SAFE)) == []


@pytest.mark.parametrize(
    ("field", "value", "name"),
    [
        ("jwt_secret", "s" * 31, "JWT_SECRET"),
        ("jwt_secret", "local-" + "s" * 40, "JWT_SECRET"),
        ("jwt_secret", "change-" + "s" * 40, "JWT_SECRET"),
        ("bootstrap_admin_password", "Admin1234!", "BOOTSTRAP_ADMIN_PASSWORD"),
        ("bootstrap_admin_password", "change-this-admin-password", "BOOTSTRAP_ADMIN_PASSWORD"),
        ("cors_allow_localhost", True, "CORS_ALLOW_LOCALHOST"),
        ("ai_server_token", "", "AI_SERVER_TOKEN"),
        ("ai_server_token", None, "AI_SERVER_TOKEN"),
        ("storage_backend", "local", "STORAGE_BACKEND"),
    ],
)
def test_production_settings_flag_each_unsafe_value(field, value, name):
    problems = unsafe_production_settings(Settings(_env_file=None, **(SAFE | {field: value})))
    assert len(problems) == 1 and problems[0].startswith(name)


def test_production_refuses_to_start_before_touching_db(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "jwt_secret", "local-secret-that-must-not-leak-1234567890")

    async def must_not_run():
        raise AssertionError("create_schema ran before the production check")

    monkeypatch.setattr(main_module, "create_schema", must_not_run)
    with pytest.raises(RuntimeError) as error, TestClient(app):
        pass
    assert "STORAGE_BACKEND" in str(error.value) and "JWT_SECRET" in str(error.value)
    assert settings.jwt_secret not in str(error.value)  # 값은 적지 않는다


def test_docs_are_closed_in_production():
    closed = main_module.docs_options("production")
    assert closed == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    assert main_module.docs_options("local") == {}


async def test_blocking_work_gets_enough_threads():
    # 몇 분 걸리는 녹음 올리기·ffmpeg 가 기본 스레드(2 vCPU 면 6개)를 다 차지해 대화의 기억 찾기가
    # 줄 서지 않게, 켤 때 넉넉한 스레드 풀을 둔다
    main_module.use_io_threads()
    ran = await asyncio.to_thread(lambda: "ok")
    executor = asyncio.get_running_loop()._default_executor
    assert ran == "ok" and executor._max_workers == main_module.IO_THREADS >= 16


def test_empty_prelaunch_persona_cap_means_unlimited():
    assert Settings(_env_file=None).prelaunch_personas_per_account == 3
    unlimited = Settings(_env_file=None, prelaunch_personas_per_account="")
    assert unlimited.prelaunch_personas_per_account is None


# ────────────────────────── 오류 응답·오류 기록 ──────────────────────────


def test_validation_error_does_not_echo_submitted_values():
    secret = "zq9!"
    with TestClient(app) as client:
        response = client.post(
            f"{API}/auth/signup",
            json={"email": _email("short"), "password": secret, "name": "x"},
        )
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "비밀번호는 8자 이상이어야 해요."
    assert body["errors"] and all(set(e) == {"loc", "msg", "type"} for e in body["errors"])
    assert secret not in response.text


def test_unexpected_error_log_keeps_type_and_head_only():
    marker = f"boom-{uuid4().hex}"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("test", 80),
            "path": "/boom",
            "query_string": b"",
            "headers": [],
        }
    )
    with TestClient(app):  # 표를 만든다
        error = ValueError(marker + "x" * 999)
        response = asyncio.run(main_module.unexpected_error(request, error))
    assert response.status_code == 500

    async def logs(db):
        query = select(ErrorLog.message).where(ErrorLog.message.contains(marker))
        return (await db.scalars(query)).all()

    assert _db(logs) == [f"ValueError: {(marker + 'x' * 999)[:300]}"]
    assert engine.sync_engine.hide_parameters is True


def test_ai_error_log_detail_is_truncated(caplog):
    response = httpx.Response(500, json={"detail": "y" * 1000})
    with caplog.at_level(logging.WARNING, logger=ai_module.logger.name):
        assert ai_module._error_message(response, "/v1/x") == ai_module.AI_TROUBLE
    logged = caplog.records[-1].getMessage()
    assert "y" * 120 in logged and "y" * 121 not in logged


# ────────────────────────── 요청 횟수 제한 ──────────────────────────


def test_sliding_window_counts_only_recent_hits():
    limit = ratelimit.RateLimit("t", 2, 60)
    assert limit.hit("a", now=0) is None
    assert limit.hit("a", now=10) is None
    assert limit.hit("a", now=20) == pytest.approx(40)  # 0초에 센 것이 60초에 빠진다
    assert limit.hit("b", now=20) is None  # 열쇠마다 따로
    assert limit.hit("a", now=60) is None
    assert limit.hit("a", now=61) is not None


def test_limiter_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(ratelimit, "MAX_KEYS", 3)
    limit = ratelimit.RateLimit("t", 5, 60)
    for key in "abc":
        limit.hit(key, now=0)
    limit.hit("d", now=100)  # 창이 지난 열쇠부터 지운다
    assert set(limit._hits) == {"d"}
    for key in "ef":
        limit.hit(key, now=101)
    limit.hit("g", now=102)  # 다 살아 있으면 적게(오래전에) 센 것부터 일부만 비운다
    assert set(limit._hits) == {"e", "f", "g"}


def test_limiter_eviction_keeps_heavily_hit_keys(monkeypatch):
    """새 열쇠를 마구 만들어도 많이 두드린 열쇠(비밀번호 맞히기 중인 이메일)의 기록은 남는다."""
    monkeypatch.setattr(ratelimit, "MAX_KEYS", 10)
    limit = ratelimit.RateLimit("t", 5, 600)
    for i in range(5):
        limit.hit("victim@example.com", now=i)
    assert limit.hit("victim@example.com", now=5) is not None  # 막혔다
    for i in range(100):
        limit.hit(f"fresh-{i}@example.com", now=10 + i)
    assert len(limit._hits) <= 10
    assert limit.hit("victim@example.com", now=200) is not None  # 여전히 막혀 있다


def test_successful_login_does_not_count_against_the_email(limits_on, monkeypatch):
    """맞는 비밀번호로 들어온 것은 이메일별 실패 횟수에 넣지 않는다."""
    monkeypatch.setattr(ratelimit.LOGIN_EMAIL, "limit", 2)
    monkeypatch.setattr(ratelimit.LOGIN_EMAIL_ANY_IP, "limit", 2)
    with TestClient(app) as client:
        email = _email("refund")
        _signup(client, email)
        for _ in range(4):
            ok = client.post(f"{API}/auth/login", json={"email": email, "password": "password123"})
            assert ok.status_code == 200, ok.text
        for _ in range(2):
            assert _login(client, email, "1.1.1.1").status_code == 401
        assert _login(client, email, "1.1.1.1").status_code == 429


def _request(headers: dict[str, str], client: str = "6.6.6.6") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (client, 1234),
        }
    )


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"CloudFront-Viewer-Address": "203.0.113.5:51234"}, "203.0.113.5"),
        ({"CloudFront-Viewer-Address": "[2001:db8::1]:443"}, "2001:db8::/64"),
        ({"CloudFront-Viewer-Address": "2001:db8:0:0:0:0:0:1:46532"}, "2001:db8::/64"),
        (
            {"CloudFront-Viewer-Address": "203.0.113.5:1", "X-Forwarded-For": "1.1.1.1"},
            "203.0.113.5",
        ),
        # 맨 앞은 고객이 마음대로 적을 수 있다. CloudFront 가 붙인 맨 뒤를 쓴다.
        ({"X-Forwarded-For": "1.1.1.1, 10.0.0.1, 198.51.100.7"}, "198.51.100.7"),
        ({}, "6.6.6.6"),
    ],
)
def test_client_ip(headers, expected):
    assert ratelimit.client_ip(_request(headers)) == expected


def _login(client: TestClient, email: str, xff: str):
    return client.post(
        f"{API}/auth/login",
        json={"email": email, "password": "wrong-password"},
        headers={"X-Forwarded-For": xff},
    )


def test_login_is_limited_per_ip_even_when_the_first_forwarded_ip_changes(limits_on):
    with TestClient(app) as client:
        for i in range(20):
            assert _login(client, _email("ip"), f"10.0.0.{i}, 203.0.113.7").status_code == 401
        blocked = _login(client, _email("ip"), "10.9.9.9, 203.0.113.7")
        assert blocked.status_code == 429
        assert blocked.json()["detail"] == ratelimit.TOO_MANY
        assert int(blocked.headers["Retry-After"]) > 0
        # 다른 사람(다른 주소)은 그대로 쓴다
        assert _login(client, _email("ip"), "203.0.113.8").status_code == 401


def test_login_is_limited_per_email(limits_on):
    email = _email("Victim")
    with TestClient(app) as client:
        # 한 IP 에서 같은 이메일로 10번 틀리면 그 IP 만 막힌다
        for _ in range(10):
            assert _login(client, email, "198.51.100.7").status_code == 401
        assert _login(client, email.upper(), "198.51.100.7").status_code == 429
        # 다른 사람(다른 IP)이 틀린 비밀번호를 쌓아도 주인의 로그인은 막히지 않는다
        assert _login(client, email, "203.0.113.9").status_code == 401


def test_login_is_limited_per_email_across_ips(limits_on):
    email = _email("Spread")
    limit = ratelimit.LOGIN_EMAIL_ANY_IP.limit
    with TestClient(app) as client:
        for i in range(limit):
            ip = f"10.{i // 250}.{i % 250}.1"
            assert _login(client, email, ip).status_code == 401
        assert _login(client, email, "192.0.2.77").status_code == 429


def test_signup_is_limited_per_ip(limits_on, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_signup", False)  # 해시 없이 빨리 센다
    body = {"email": _email("s"), "password": "password123", "name": "x"}
    with TestClient(app) as client:
        for _ in range(10):
            assert client.post(f"{API}/auth/signup", json=body).status_code == 403
        assert client.post(f"{API}/auth/signup", json=body).status_code == 429


def test_refresh_is_limited_and_can_be_switched_off(limits_on, monkeypatch):
    from app.core import ratelimit

    with TestClient(app) as client:
        for _ in range(ratelimit.REFRESH_IP.limit):
            sent = client.post(f"{API}/auth/refresh", json={"refreshToken": "nope"})
            assert sent.status_code == 401
        assert client.post(f"{API}/auth/refresh", json={"refreshToken": "nope"}).status_code == 429
        monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)
        assert client.post(f"{API}/auth/refresh", json={"refreshToken": "nope"}).status_code == 401


def test_analytics_events_are_limited_per_user(limits_on):
    with TestClient(app) as client:
        h = _admin(client)
        for _ in range(120):
            sent = client.post(f"{API}/analytics/events", headers=h, json={"name": "nope"})
            assert sent.status_code == 204
        sent = client.post(f"{API}/analytics/events", headers=h, json={"name": "nope"})
        assert sent.status_code == 429


def test_every_limited_endpoint_is_wired(limits_on, monkeypatch):
    """상한을 0 으로 두면 첫 요청부터 429. 각 경로가 제한을 부르는지만 본다(없는 id 여도 센다)."""
    wav = ("v.wav", b"RIFF", "audio/wav")
    with TestClient(app) as client:
        h = _admin(client)  # 상한을 내리기 전에 로그인해 둔다
        for limit in ratelimit.ALL:
            monkeypatch.setattr(limit, "limit", 0)
        login = {"email": _email("x"), "password": "12345678"}
        calls = {
            "login": client.post(f"{API}/auth/login", json=login),
            "signup": client.post(f"{API}/auth/signup", json=login | {"name": "x"}),
            "refresh": client.post(f"{API}/auth/refresh", json={"refreshToken": "x"}),
            "exchange": client.post(f"{API}/auth/oauth/exchange", json={"ticket": "x"}),
            "text": client.post(
                f"{API}/conversations/c/messages/text", headers=h, json={"content": "a"}
            ),
            "voice": client.post(
                f"{API}/conversations/c/messages/voice", headers=h, files={"file": wav}
            ),
            "speech": client.post(f"{API}/messages/m/speech", headers=h),
            "recordings": client.post(
                f"{API}/personas/p/recordings", headers=h, files={"files": wav}
            ),
            "materials": client.post(
                f"{API}/personas/p/materials", headers=h, data={"kind": "sms"}
            ),
            "analytics": client.post(f"{API}/analytics/events", headers=h, json={"name": "x"}),
        }
    assert {name: r.status_code for name, r in calls.items()} == dict.fromkeys(calls, 429)


# ────────────────────────── 본문 크기 상한 ──────────────────────────


def test_body_caps_fit_each_endpoint_limit():
    limit = body_limit.limit_for
    recordings = persona_setup.MAX_RECORDINGS * get_settings().max_upload_mb * MiB
    assert body_limit.RECORDING_FILES == persona_setup.MAX_RECORDINGS
    assert limit(f"{API}/personas/p/recordings") > recordings
    assert limit(f"{API}/personas/p/recordings") >= 2 * 1024 * MiB
    pasted = (materials.MAX_PASTE_CHARS + 1000) * 4  # UTF-8 한 글자 최대 4바이트
    assert limit(f"{API}/personas/p/materials") > materials.MAX_REQUEST_MB * MiB + pasted
    assert limit(f"{API}/conversations/c/messages/voice") > conversations.VOICE_MAX_BYTES
    for path in (f"{API}/auth/login", f"{API}/personas/p/recordings/apply", "/health"):
        assert limit(path) == 2 * MiB


def test_declared_content_length_over_cap_is_refused_before_reading():
    with TestClient(app) as client:
        response = client.post(
            f"{API}/auth/login",
            content=b"x" * (2 * MiB + 1),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json() == {"detail": "파일이 너무 커요."}


def test_too_large_response_keeps_cors_headers():
    """웹 화면이 413 을 읽을 수 있게 본문 상한은 CORS 안쪽에 있다."""
    if not get_settings().cors_allow_localhost:
        pytest.skip("CORS_ALLOW_LOCALHOST=false 인 .env")
    origin = "http://localhost:5555"
    with TestClient(app) as client:
        response = client.post(
            f"{API}/auth/login",
            content=b"x" * (2 * MiB + 1),
            headers={"Content-Type": "application/json", "Origin": origin},
        )
    assert response.status_code == 413
    assert response.headers.get("access-control-allow-origin") == origin


def test_streamed_body_without_length_is_cut_at_cap():
    def chunks():
        for _ in range(3):
            yield b" " * MiB

    with TestClient(app) as client:
        response = client.post(
            f"{API}/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 413
    assert response.json() == {"detail": "파일이 너무 커요."}


class _Payload(BaseModel):
    text: str


def _mini_app(ran: list[str]) -> body_limit.BodySizeLimit:
    mini = FastAPI()

    @mini.post("/json")
    async def json_route(payload: _Payload):
        ran.append("json")
        return {}

    @mini.post("/form")
    async def form_route(file: Annotated[UploadFile, File()], kind: Annotated[str, Form()]):
        ran.append("form")
        return {}

    return body_limit.BodySizeLimit(mini)


def _call_chunked(asgi, path: str, chunks: list[bytes], content_type: str) -> tuple[int, bytes]:
    """Content-Length 없이 여러 번에 나눠 보낸다(chunked)."""
    queue = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
        for i, c in enumerate(chunks)
    ]
    sent: list[dict] = []

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", content_type.encode()), (b"host", b"test")],
        "client": ("1.2.3.4", 1),
        "server": ("test", 80),
    }
    asyncio.run(asgi(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], body


def test_chunked_json_and_multipart_bodies_stop_at_cap():
    ran: list[str] = []
    asgi = _mini_app(ran)
    status, body = _call_chunked(asgi, "/json", [b" " * MiB] * 3, "application/json")
    assert status == 413 and "파일이 너무 커요." in body.decode()

    boundary = "XyZ"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="kind"\r\n\r\nsms\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode()
    chunks = [head] + [b"a" * MiB] * 3 + [f"\r\n--{boundary}--\r\n".encode()]
    status, body = _call_chunked(asgi, "/form", chunks, f"multipart/form-data; boundary={boundary}")
    assert status == 413 and "파일이 너무 커요." in body.decode()
    assert ran == []  # 화면 코드까지 가지 않았다

    small = [head, b"a" * 10, f"\r\n--{boundary}--\r\n".encode()]
    status, _ = _call_chunked(asgi, "/form", small, f"multipart/form-data; boundary={boundary}")
    assert status == 200 and ran == ["form"]


def _declared(asgi, path: str, length: int, token: str | None) -> int:
    """본문은 비우고 Content-Length 만 크게 밝혀 보낸다(상한은 본문을 읽기 전에 정해진다)."""
    headers = [
        (b"content-type", b"application/octet-stream"),
        (b"content-length", str(length).encode()),
    ]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("1.2.3.4", 1),
        "server": ("test", 80),
    }
    asyncio.run(asgi(scope, receive, send))
    return next(m for m in sent if m["type"] == "http.response.start")["status"]


def test_big_uploads_need_a_valid_login_before_the_body_is_read():
    from app.core.security import create_access_token, create_refresh_token

    asgi = _mini_app([])
    path, big = "/p/recordings", 3 * MiB
    assert _declared(asgi, path, big, None) == 401
    assert _declared(asgi, path, big, "not-a-token") == 401
    assert _declared(asgi, path, big, create_refresh_token("u", 0)) == 401  # access 토큰만
    # 로그인한 요청은 상한(GB)까지 통과해 라우트로 간다(이 작은 앱엔 그 라우트가 없어 404)
    assert _declared(asgi, path, big, create_access_token("u", 0)) == 404
    # 작은 본문은 토큰 없이도 라우트까지 간다(라우트가 401 을 준다)
    assert _declared(asgi, path, 1000, None) == 404


def test_big_uploads_are_limited_to_a_few_at_once_and_explain_the_limit():
    from app.core.security import create_access_token

    asgi = _mini_app([])
    token = create_access_token("u", 0)
    asgi.big_uploads = body_limit.MAX_BIG_UPLOADS
    assert _declared(asgi, "/p/recordings", 200 * MiB, token) == 429
    # 100MB 아래(예전에는 세지 않았다)도 센다
    assert _declared(asgi, "/p/recordings", 99 * MiB, token) == 429
    assert _declared(asgi, "/p/materials", 20 * MiB, token) == 429
    # 작은 목소리 메시지(8MB 아래)는 세지 않는다
    assert _declared(asgi, "/c/messages/voice", MiB, token) == 404
    asgi.big_uploads = 0
    # 한 사람이 동시에 올릴 수 있는 수
    asgi.big_by_user = {"u": body_limit.MAX_BIG_UPLOADS_PER_USER}
    assert _declared(asgi, "/p/recordings", 50 * MiB, token) == 429
    assert _declared(asgi, "/p/recordings", 50 * MiB, create_access_token("v", 0)) == 404
    asgi.big_by_user = {}
    # 동시에 받는 바이트 합
    asgi.big_bytes = body_limit.MAX_BIG_BYTES - 10 * MiB
    assert _declared(asgi, "/p/recordings", 50 * MiB, token) == 429
    asgi.big_bytes = 0
    assert _declared(asgi, "/p/recordings", 200 * MiB, token) == 404
    assert asgi.big_uploads == 0 and asgi.big_bytes == 0  # 끝나면 자리를 돌려준다
    assert asgi.big_by_user == {}
    assert "100MB" in body_limit.too_large_message(f"{API}/personas/p/materials")
    assert "나눠서" in body_limit.too_large_message(f"{API}/personas/p/recordings")
    assert body_limit.too_large_message(f"{API}/auth/login") == "파일이 너무 커요."


def _admin_conversation(client: TestClient, h: dict) -> str:
    persona = client.get(f"{API}/personas/active", headers=h).json()["data"]
    created = client.post(
        f"{API}/conversations",
        headers=h,
        json={"personaId": persona["id"], "channel": "TEXT", "title": "hardening"},
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


def test_undeclared_big_upload_takes_a_slot():
    """길이를 밝히지 않은(chunked) 업로드도 큰 업로드로 센다(예전에는 세지 않아 얼마든지 받았다)."""
    from app.core.security import create_access_token

    asgi = _mini_app([])
    asgi.big_uploads = body_limit.MAX_BIG_UPLOADS
    token = create_access_token("u", 0)
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/p/recordings",
        "raw_path": b"/p/recordings",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"multipart/form-data; boundary=x"),
            (b"authorization", f"Bearer {token}".encode()),
        ],
        "client": ("1.2.3.4", 1),
        "server": ("test", 80),
    }
    asyncio.run(asgi(scope, receive, send))
    assert next(m for m in sent if m["type"] == "http.response.start")["status"] == 429


def test_voice_message_over_the_size_cap_is_refused():
    with TestClient(app) as client:
        h = _admin(client)
        cid = _admin_conversation(client, h)
        big = b"\x00" * (conversations.VOICE_MAX_BYTES + 10)
        response = client.post(
            f"{API}/conversations/{cid}/messages/voice",
            headers=h,
            files={"file": ("long.webm", big, "audio/webm")},
        )
    assert response.status_code == 413
    assert "녹음이 너무 길어요" in response.json()["detail"]


def test_voice_message_storage_ignores_client_name_and_type(monkeypatch):
    stored: list[tuple[str, str | None]] = []

    class Spy:
        def put(self, key, content, content_type=None):
            stored.append((key, content_type))

    monkeypatch.setattr(conversations, "get_storage", lambda: Spy())
    with TestClient(app) as client:
        h = _admin(client)
        cid = _admin_conversation(client, h)
        for name, kind in (("evil.html", "text/html"), ("memo.M4A", "text/html"), ("noext", None)):
            response = client.post(
                f"{API}/conversations/{cid}/messages/voice",
                headers=h,
                files={"file": (name, b"voice-bytes", kind or "application/octet-stream")},
            )
            assert response.status_code == 200, response.text
    assert [(Path(k).suffix, t) for k, t in stored] == [
        (".webm", "audio/webm"),
        (".m4a", "audio/mp4"),
        (".webm", "audio/webm"),
    ]
    assert set(audio.AUDIO_CONTENT_TYPES) == audio.AUDIO_SUFFIXES


# ────────────────────────── 로그인·관리자·대화 상대 ──────────────────────────


def test_unknown_email_still_checks_a_password_hash(monkeypatch):
    seen: list[str] = []
    real = auth_module.verify_password

    def spy(password, hashed):
        seen.append(hashed)
        return real(password, hashed)

    monkeypatch.setattr(auth_module, "verify_password", spy)
    with TestClient(app) as client:
        response = client.post(
            f"{API}/auth/login", json={"email": _email("nobody"), "password": "password123"}
        )
    assert response.status_code == 401
    assert len(seen) == 1 and seen[0].startswith("$argon2")


def test_invited_user_becomes_active_on_first_login():
    email = _email("invited")
    with TestClient(app) as client:
        admin = _admin(client)
        invited = client.post(
            f"{API}/admin/users/invitations",
            headers=admin,
            json={"email": email, "name": "초대", "role": "USER"},
        )
        assert invited.status_code == 201, invited.text
        temporary = invited.json()["data"]["temporaryPassword"]
        wrong = client.post(f"{API}/auth/login", json={"email": email, "password": "x" * 12})
        assert wrong.status_code == 401
        assert _status(email) == UserStatus.INVITED
        login = client.post(f"{API}/auth/login", json={"email": email, "password": temporary})
        assert login.status_code == 200, login.text
        h = {"Authorization": f"Bearer {login.json()['data']['bearerToken']}"}
        assert client.get(f"{API}/auth/me", headers=h).status_code == 200
    assert _status(email) == UserStatus.ACTIVE


def test_admin_activating_a_persona_only_touches_that_owner(monkeypatch):
    monkeypatch.setattr(get_settings(), "prelaunch_personas_per_account", None)
    with TestClient(app) as client:
        admin = _admin(client)
        admin_active = client.get(f"{API}/personas/active", headers=admin).json()["data"]["id"]
        h = _signup(client, _email("owner"))
        first, second = (
            client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
            for _ in range(2)
        )
        for pid in (first, second):
            patched = client.patch(
                f"{API}/admin/personas/{pid}", headers=admin, json={"isActive": True}
            )
            assert patched.status_code == 200, patched.text
        # 관리자(다른 사람)의 켜진 대화 상대는 그대로다
        assert client.get(f"{API}/personas/active", headers=admin).json()["data"]["id"] == (
            admin_active
        )
        assert client.get(f"{API}/personas/active", headers=h).json()["data"]["id"] == second

    async def active(db):
        query = select(Persona.id).where(Persona.id.in_([first, second]), Persona.is_active)
        return (await db.scalars(query)).all()

    assert _db(active) == [second]


def test_prelaunch_caps_personas_per_account(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "payments_open", False)
    monkeypatch.setattr(settings, "prelaunch_personas_per_account", 3)
    with TestClient(app) as client:
        h = _signup(client, _email("cap"))
        for _ in range(3):
            assert client.post(f"{API}/personas", headers=h, json=BASICS).status_code == 201
        refused = client.post(f"{API}/personas", headers=h, json=BASICS)
        assert refused.status_code == 409
        assert refused.json()["detail"] == "지금은 대화 상대를 3명까지 만들 수 있어요."
        # 지우면 다시 만들 수 있다(지금 가진 수만 센다)
        first = client.get(f"{API}/personas", headers=h).json()["data"][0]["id"]
        assert client.delete(f"{API}/personas/{first}", headers=h).status_code == 200
        assert client.post(f"{API}/personas", headers=h, json=BASICS).status_code == 201
        # 관리자는 막지 않는다(처음 관리자 계정을 어지르지 않게 새 관리자를 초대해 쓴다)
        admin_email = _email("admin")
        invited = client.post(
            f"{API}/admin/users/invitations",
            headers=_admin(client),
            json={"email": admin_email, "name": "관리자", "role": "ADMIN"},
        )
        password = invited.json()["data"]["temporaryPassword"]
        login = client.post(f"{API}/auth/login", json={"email": admin_email, "password": password})
        admin = {"Authorization": f"Bearer {login.json()['data']['bearerToken']}"}
        for _ in range(4):
            assert client.post(f"{API}/personas", headers=admin, json=BASICS).status_code == 201
        # 비우면 제한 없음, 결제가 열리면 이 상한은 쓰지 않는다
        monkeypatch.setattr(settings, "prelaunch_personas_per_account", None)
        assert client.post(f"{API}/personas", headers=h, json=BASICS).status_code == 201
        monkeypatch.setattr(settings, "prelaunch_personas_per_account", 1)
        monkeypatch.setattr(settings, "payments_open", True)
        assert client.post(f"{API}/personas", headers=h, json=BASICS).status_code == 201


def test_csv_export_neutralizes_formulas():
    rows = [["=HYPERLINK(1)", "+1", "-2", "@SUM(A1)", "\tx", "\rx", "ok", 5, None]]
    response = stats._csv(["a", "b", "c", "d", "e", "f", "g", "h", "i"], rows, "t.csv")
    text = response.body.decode("utf-8").lstrip("﻿")
    parsed = list(csv.reader(io.StringIO(text, newline="")))
    assert parsed[1] == ["'=HYPERLINK(1)", "'+1", "'-2", "'@SUM(A1)", "'\tx", "'\rx", "ok", "5", ""]


# ────────────────────────── ffmpeg ──────────────────────────


def test_ffmpeg_timeout_becomes_a_friendly_error(monkeypatch):
    def slow(*args, **kwargs):
        assert kwargs["timeout"] == audio.FFMPEG_TIMEOUT_SECONDS == 900
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(audio.subprocess, "run", slow)
    with pytest.raises(audio.AudioError, match="너무 오래"):
        audio._run(["ffmpeg", "-version"])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
async def test_ffmpeg_runs_end_to_end_with_protocol_whitelist(monkeypatch):
    commands: list[list[str]] = []
    real = subprocess.run

    def spy(args, **kwargs):
        commands.append(list(args))
        return real(args, **kwargs)

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "sine.wav"
        real(
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi"]
            + ["-i", "sine=frequency=440:duration=2", str(wav)],
            check=True,
        )
        monkeypatch.setattr(audio.subprocess, "run", spy)
        assert abs(audio._duration_ms(wav) - 2000) <= 60
        content, duration = await audio.extract_audio(wav.read_bytes(), ".wav")
    # AAC 앞뒤 여백(priming)은 ffmpeg 빌드마다 조금 달라 정확히 2000 이 아닐 수 있다
    assert abs(duration - 2000) <= 60 and content
    preview = await audio.build_preview({"a": content}, [("a", 0, 1500), ("a", 500, 1900)])
    assert preview[:3] == b"ID3"
    await audio.is_phone_band(content)
    assert {c[0] for c in commands} == {"ffmpeg", "ffprobe"}
    for command in commands:
        inputs = [i for i, arg in enumerate(command) if arg == "-i"]
        assert inputs, command
        for i in inputs:
            assert command[i - 2 : i] == ["-protocol_whitelist", "file,pipe"], command
