"""카카오·구글 로그인: start → (그 회사) → callback → 한 번짜리 표 → exchange → 토큰.

그 회사와 주고받는 부분(fetch_profile)은 가짜로 바꾼다. 네트워크를 쓰지 않는다.
"""

import json
from datetime import timedelta
from urllib.parse import parse_qs, quote, unquote, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import auth as auth_module
from app.core.config import get_settings
from app.core.security import decode_oauth_state
from app.main import app
from app.services import oauth as oauth_module
from app.services.oauth import OAuthError, OAuthProfile

AUTH = "/api/v1/auth"
FRONT = "http://localhost:8090/"


class FakeProvider:
    """카카오·구글 대역. code 마다 돌려줄 프로필을 정해 두고, 받은 호출을 모아 둔다."""

    def __init__(self) -> None:
        self.profiles: dict[str, OAuthProfile] = {}
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, provider: str, code: str, redirect_uri: str) -> OAuthProfile:
        self.calls.append((str(provider), code, redirect_uri))
        if code not in self.profiles:
            raise OAuthError("unknown code")
        return self.profiles[code]


@pytest.fixture(autouse=True)
def oauth_settings(monkeypatch: pytest.MonkeyPatch):
    """.env 값과 상관없이 같은 설정으로 돌린다."""
    settings = get_settings()
    for key, value in {
        "kakao_client_id": "kakao-id",
        "kakao_client_secret": "kakao-secret",
        "google_client_id": "google-id",
        "google_client_secret": "google-secret",
        "public_base_url": "http://localhost:8000",
        "frontend_url": "http://localhost:8090",
        "cors_origins": ["https://talkto.example"],
        "cors_allow_localhost": True,
        "allow_signup": True,
    }.items():
        monkeypatch.setattr(settings, key, value)
    return settings


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    monkeypatch.setattr(oauth_module, "fetch_profile", provider)
    return provider


@pytest.fixture
def client():
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def _profile(provider: str = "kakao", **kw) -> OAuthProfile:
    values = {
        "subject": uuid4().hex,
        "email": None,
        "email_verified": False,
        "name": "홍길동",
    } | kw
    return OAuthProfile(provider=provider, **values)


def _start(client: TestClient, provider: str = "kakao", return_to: str = FRONT) -> str:
    response = client.get(f"{AUTH}/oauth/{provider}/start", params={"return_to": return_to})
    assert response.status_code == 302, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def _social_login(
    client: TestClient, fake: FakeProvider, profile: OAuthProfile, **params
) -> httpx.Response:
    """start → (가짜 회사) → callback 까지. callback 응답을 돌려준다."""
    state = _start(client, profile.provider)
    code = f"code-{uuid4().hex}"
    fake.profiles[code] = profile
    return client.get(
        f"{AUTH}/oauth/{profile.provider}/callback",
        params={"code": code, "state": state} | params,
    )


def _ticket(response: httpx.Response) -> str:
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{FRONT}#/auth?ticket="), location
    return location.split("ticket=", 1)[1]


def _error(response: httpx.Response) -> str:
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    prefix = f"{FRONT}#/login?error="
    assert location.startswith(prefix), location
    return unquote(location[len(prefix) :])


def _exchange(client: TestClient, ticket: str) -> dict:
    response = client.post(f"{AUTH}/oauth/exchange", json={"ticket": ticket})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["refreshToken"]
    return {"Authorization": f"Bearer {data['bearerToken']}"}


def _me(client: TestClient, headers: dict) -> dict:
    response = client.get(f"{AUTH}/me", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_providers_follow_settings(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    data = client.get(f"{AUTH}/providers").json()["data"]
    assert data == {"kakao": True, "google": True, "signup": True}

    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_secret", None)
    monkeypatch.setattr(settings, "kakao_client_id", "  ")
    monkeypatch.setattr(settings, "allow_signup", False)
    data = client.get(f"{AUTH}/providers").json()["data"]
    assert data == {"kakao": False, "google": False, "signup": False}


def test_start_redirects_to_provider_with_state_and_cookie(client: TestClient):
    response = client.get(
        f"{AUTH}/oauth/kakao/start", params={"return_to": "http://localhost:8090/#/login"}
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://kauth.kakao.com/oauth/authorize?")
    query = parse_qs(urlsplit(location).query)
    assert query["client_id"] == ["kakao-id"]
    assert query["redirect_uri"] == ["http://localhost:8000/api/v1/auth/oauth/kakao/callback"]
    assert query["response_type"] == ["code"]

    claims = decode_oauth_state(query["state"][0])
    assert claims["provider"] == "kakao"
    assert claims["return_to"] == "http://localhost:8090/"  # #조각은 버린다
    assert claims["nonce"] == client.cookies.get("talkto_oauth_kakao")
    cookie = response.headers["set-cookie"].lower()
    for part in ("talkto_oauth_kakao=", "httponly", "path=/api/v1/auth/oauth", "samesite=lax"):
        assert part in cookie
    assert "max-age=600" in cookie
    assert "secure" not in cookie  # http 로컬에서는 secure 를 붙이면 쿠키가 안 돌아온다

    google = client.get(f"{AUTH}/oauth/google/start", params={"return_to": FRONT})
    location = google.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "scope=openid%20email%20profile" in location
    query = parse_qs(urlsplit(location).query)
    assert query["client_id"] == ["google-id"]
    assert query["redirect_uri"] == ["http://localhost:8000/api/v1/auth/oauth/google/callback"]
    assert query["prompt"] == ["select_account"]
    assert decode_oauth_state(query["state"][0])["provider"] == "google"


def test_start_only_returns_to_our_web(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    for bad in [
        "https://evil.example/",
        "http://localhost:8090@evil.example/",
        "https://localhost:8090/",
        "//evil.example/",
        "javascript:alert(1)",
        "http://localhost:8090\\@evil.example/",
    ]:
        response = client.get(f"{AUTH}/oauth/kakao/start", params={"return_to": bad})
        assert response.status_code == 400, bad
        assert "talkto_oauth" not in response.headers.get("set-cookie", "")

    # CORS_ORIGINS 와 FRONTEND_URL 은 언제나 된다. localhost 아무 포트는 로컬 설정일 때만.
    monkeypatch.setattr(get_settings(), "cors_allow_localhost", False)
    for good in ["https://talkto.example/app/", "http://localhost:8090/", "http://LOCALHOST:8090"]:
        response = client.get(f"{AUTH}/oauth/kakao/start", params={"return_to": good})
        assert response.status_code == 302, good
    response = client.get(
        f"{AUTH}/oauth/kakao/start", params={"return_to": "http://localhost:5555/"}
    )
    assert response.status_code == 400


def test_unconfigured_provider_goes_back_with_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(get_settings(), "kakao_client_secret", None)
    response = client.get(f"{AUTH}/oauth/kakao/start", params={"return_to": FRONT})
    assert response.status_code == 302
    text = "카카오 로그인은 아직 쓸 수 없어요. 다른 방법으로 로그인해 주세요."
    assert response.headers["location"] == f"{FRONT}#/login?error={quote(text, safe='')}"
    assert _error(response) == text


def test_full_flow_ticket_is_single_use(client: TestClient, fake: FakeProvider):
    profile = _profile("kakao", name="  손주  ")
    response = _social_login(client, fake, profile)
    ticket = _ticket(response)
    assert fake.calls[-1][2] == "http://localhost:8000/api/v1/auth/oauth/kakao/callback"
    # 쿠키는 한 번 쓰고 지운다
    assert 'talkto_oauth_kakao=""' in response.headers["set-cookie"]
    assert "talkto_oauth_kakao" not in client.cookies

    headers = _exchange(client, ticket)
    me = _me(client, headers)
    assert me["provider"] == "kakao"
    assert me["email"] is None  # 이메일을 받지 못한 사람은 자리 표시 주소를 숨긴다
    assert me["name"] == "손주"

    again = client.post(f"{AUTH}/oauth/exchange", json={"ticket": ticket})
    assert again.status_code == 401
    assert again.json()["detail"] == "로그인 시간이 지났어요. 다시 시도해 주세요."

    # 같은 카카오 계정으로 다시 들어오면 같은 사람이다
    second = _me(client, _exchange(client, _ticket(_social_login(client, fake, profile))))
    assert second["id"] == me["id"]


def test_expired_ticket_is_rejected(
    client: TestClient, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(auth_module, "TICKET_TTL", timedelta(seconds=-1))
    ticket = _ticket(_social_login(client, fake, _profile("google")))
    response = client.post(f"{AUTH}/oauth/exchange", json={"ticket": ticket})
    assert response.status_code == 401
    assert client.post(f"{AUTH}/oauth/exchange", json={"ticket": "nope"}).status_code == 401


def test_callback_checks_state_and_cookie(client: TestClient, fake: FakeProvider):
    # 다른 브라우저(쿠키 없음)에서 남이 시작한 state 로 들어오면 로그인하지 않는다
    state = _start(client, "kakao")
    client.cookies.clear()
    fake.profiles["c1"] = _profile("kakao")
    response = client.get(f"{AUTH}/oauth/kakao/callback", params={"code": "c1", "state": state})
    assert "처음부터 다시 로그인" in _error(response)
    assert fake.calls == []

    # 쿠키가 다른 값이어도 마찬가지
    state = _start(client, "kakao")
    client.cookies.set("talkto_oauth_kakao", "someone-else", path="/api/v1/auth/oauth")
    response = client.get(f"{AUTH}/oauth/kakao/callback", params={"code": "c1", "state": state})
    assert "처음부터 다시 로그인" in _error(response)
    assert fake.calls == []

    # state 가 망가졌거나 다른 회사 것이면 돌아갈 곳을 믿을 수 없어 400
    assert client.get(f"{AUTH}/oauth/kakao/callback", params={"code": "c1"}).status_code == 400
    bad = client.get(f"{AUTH}/oauth/kakao/callback", params={"code": "c1", "state": "x.y.z"})
    assert bad.status_code == 400
    google_state = _start(client, "google")
    wrong = client.get(f"{AUTH}/oauth/kakao/callback", params={"code": "c1", "state": google_state})
    assert wrong.status_code == 400


def test_cancel_and_provider_failure_go_back_to_login(client: TestClient, fake: FakeProvider):
    state = _start(client, "kakao")
    response = client.get(
        f"{AUTH}/oauth/kakao/callback", params={"error": "access_denied", "state": state}
    )
    assert _error(response) == "로그인을 취소했어요."
    assert fake.calls == []

    # 그 회사와 주고받다 실패 (가짜가 모르는 code)
    state = _start(client, "google")
    response = client.get(f"{AUTH}/oauth/google/callback", params={"code": "??", "state": state})
    assert _error(response) == "구글 로그인에 실패했어요. 잠시 후 다시 시도해 주세요."


def _password_signup(client: TestClient, email: str) -> str:
    response = client.post(
        f"{AUTH}/signup", json={"email": email, "password": "password123", "name": "홍길동"}
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['data']['bearerToken']}"}
    return _me(client, headers)["id"]


def test_verified_email_links_only_social_verified_accounts(client: TestClient, fake: FakeProvider):
    # 비밀번호 가입은 이메일을 확인하지 않는다. 남이 먼저 내 이메일로 가입해 둔 계정에 붙지 않는다.
    squatter_id = _password_signup(client, "link-me@example.com")
    profile = _profile("google", email="Link-Me@Example.com", email_verified=True)
    me = _me(client, _exchange(client, _ticket(_social_login(client, fake, profile))))
    assert me["id"] != squatter_id
    assert me["provider"] == "google"
    assert me["email"] is None

    # 소셜로 확인된 적 있는 계정에는 다른 회사 로그인도 같은 인증 이메일이면 잇는다
    first = _profile("google", email="fresh@example.com", email_verified=True, name=None)
    first_me = _me(client, _exchange(client, _ticket(_social_login(client, fake, first))))
    assert first_me["email"] == "fresh@example.com"
    assert first_me["name"] == "사용자"
    second = _profile("kakao", email="fresh@example.com", email_verified=True)
    second_me = _me(client, _exchange(client, _ticket(_social_login(client, fake, second))))
    assert second_me["id"] == first_me["id"]

    # 인증 안 된 이메일로는 남의 계정에 붙지 않는다. 새 사람으로, 이메일 없이 만든다.
    other = _profile("kakao", email="fresh@example.com", email_verified=False)
    me = _me(client, _exchange(client, _ticket(_social_login(client, fake, other))))
    assert me["id"] != first_me["id"]
    assert me["email"] is None


def test_signup_closed_blocks_only_new_social_users(
    client: TestClient, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
):
    existing = _profile("kakao")
    first_id = _me(client, _exchange(client, _ticket(_social_login(client, fake, existing))))["id"]

    monkeypatch.setattr(get_settings(), "allow_signup", False)
    assert _error(_social_login(client, fake, _profile("kakao"))) == "지금은 가입을 받지 않아요."
    again = _me(client, _exchange(client, _ticket(_social_login(client, fake, existing))))
    assert again["id"] == first_id


def test_suspended_user_cannot_use_social_login(client: TestClient, fake: FakeProvider):
    settings = get_settings()
    profile = _profile("google")
    user_id = _me(client, _exchange(client, _ticket(_social_login(client, fake, profile))))["id"]

    login = client.post(
        f"{AUTH}/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    admin = {"Authorization": f"Bearer {login.json()['data']['bearerToken']}"}
    # 관리자 목록도 자리 표시 이메일에서 깨지지 않고, 어떤 로그인인지 보인다
    users = client.get("/api/v1/admin/users", headers=admin).json()["data"]
    row = next(user for user in users if user["id"] == user_id)
    assert "email" not in row  # 관리자 목록은 가린 이메일(emailMasked)만 싣는다
    assert row["provider"] == "google"

    suspended = client.patch(
        f"/api/v1/admin/users/{user_id}", headers=admin, json={"status": "SUSPENDED"}
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["data"]["provider"] == "google"
    assert _error(_social_login(client, fake, profile)) == "사용할 수 없는 계정이에요."


async def test_provider_http_calls(monkeypatch: pytest.MonkeyPatch):
    """카카오·구글 답 모양을 OAuthProfile 하나로 맞추는지. 가짜 HTTP 로 본다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if url == oauth_module.KAKAO_TOKEN_URL or url == oauth_module.GOOGLE_TOKEN_URL:
            form = parse_qs(request.content.decode())
            if form["code"] == ["bad"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": f"at-{form['client_id'][0]}"})
        if url == oauth_module.KAKAO_PROFILE_URL:
            return httpx.Response(
                200,
                json={
                    "id": 4242,
                    "kakao_account": {
                        "email": "k@example.com",
                        "is_email_verified": True,
                        "is_email_valid": False,
                        "profile": {},
                    },
                    "properties": {"nickname": "카카오닉"},
                },
            )
        if url == oauth_module.GOOGLE_PROFILE_URL:
            return httpx.Response(
                200,
                content=json.dumps(
                    {"sub": "g-1", "email": "g@example.com", "email_verified": True, "name": "구글"}
                ),
            )
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        oauth_module.httpx,
        "AsyncClient",
        lambda *a, **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    redirect = "http://localhost:8000/api/v1/auth/oauth/kakao/callback"

    kakao = await oauth_module.kakao_profile("code-1", redirect)
    assert kakao == OAuthProfile("kakao", "4242", "k@example.com", False, "카카오닉")
    form = parse_qs(seen[0].content.decode())
    assert form == {
        "grant_type": ["authorization_code"],
        "client_id": ["kakao-id"],
        "client_secret": ["kakao-secret"],
        "redirect_uri": [redirect],
        "code": ["code-1"],
    }
    assert seen[1].headers["authorization"] == "Bearer at-kakao-id"

    google = await oauth_module.google_profile("code-2", redirect)
    assert google == OAuthProfile("google", "g-1", "g@example.com", True, "구글")
    assert seen[3].headers["authorization"] == "Bearer at-google-id"

    with pytest.raises(OAuthError):
        await oauth_module.google_profile("bad", redirect)


def test_starting_another_provider_does_not_break_the_first(client: TestClient, fake: FakeProvider):
    """구글 로그인 창을 열어 둔 채 카카오 로그인을 시작해도 구글 로그인이 끝까지 된다."""
    profile = _profile("google", email="both@example.com", email_verified=True)
    google_state = _start(client, "google")
    _start(client, "kakao")
    fake.profiles["g-code"] = profile
    response = client.get(
        f"{AUTH}/oauth/google/callback", params={"code": "g-code", "state": google_state}
    )
    assert "#/auth?ticket=" in response.headers["location"], response.headers["location"]


def test_leftover_old_cookie_does_not_break_login(client: TestClient, fake: FakeProvider):
    """예전 이름(talkto_oauth) 쿠키가 브라우저에 남아 있어도 로그인이 된다."""
    client.cookies.set("talkto_oauth", "stale", path="/api/v1/auth/oauth")
    client.cookies.set("talkto_oauth", "stale", path="/api/v1/auth/oauth/kakao")
    state = _start(client, "kakao")
    fake.profiles["k-code"] = _profile("kakao")
    response = client.get(f"{AUTH}/oauth/kakao/callback", params={"code": "k-code", "state": state})
    assert "#/auth?ticket=" in response.headers["location"], response.headers["location"]


def test_social_signup_and_login_are_counted(client: TestClient, fake: FakeProvider):
    """사용 통계: 처음 들어오면 signup, 다음부터는 login (method = kakao/google)."""
    import asyncio

    from sqlalchemy import select

    from app.db.models import AnalyticsEvent
    from app.db.session import SessionLocal

    def names(user_id: str) -> list[tuple[str, dict]]:
        async def go():
            async with SessionLocal() as db:
                rows = await db.scalars(
                    select(AnalyticsEvent)
                    .where(AnalyticsEvent.user_id == user_id)
                    .order_by(AnalyticsEvent.created_at)
                )
                return [(e.name, e.props) for e in rows.all()]

        return asyncio.run(go())

    profile = _profile("google", email="stats-social@example.com", email_verified=True)
    headers = _exchange(client, _ticket(_social_login(client, fake, profile)))
    user_id = _me(client, headers)["id"]
    _ticket(_social_login(client, fake, profile))
    events = names(user_id)
    assert ("signup", {"method": "google"}) in events
    assert ("login", {"method": "google"}) in events
    assert all("stats-social" not in str(props) for _, props in events)


def test_kakao_email_arrives_later_and_replaces_the_blank(client: TestClient, fake: FakeProvider):
    """비즈 앱 전에 이메일 없이 가입한 카카오 사용자가 나중에 인증된 이메일을 주면 바꾼다."""
    subject = uuid4().hex
    first = _social_login(client, fake, _profile(subject=subject))
    h = _exchange(client, _ticket(first))
    assert _me(client, h)["email"] is None or "@social.talkto.invalid" in (
        _me(client, h)["email"] or ""
    )
    email = f"kakao-{uuid4().hex[:8]}@example.com"
    again = _social_login(client, fake, _profile(subject=subject, email=email, email_verified=True))
    h = _exchange(client, _ticket(again))
    assert _me(client, h)["email"] == email


def test_kakao_extra_scope_is_requested_only_when_set(client: TestClient, monkeypatch):
    from app.core.config import get_settings

    # 로컬 .env 에 켜 두었어도 '안 켰을 때'부터 본다
    monkeypatch.setattr(get_settings(), "kakao_extra_scope", None)
    response = client.get(f"{AUTH}/oauth/kakao/start", params={"return_to": FRONT})
    assert "scope=" not in response.headers["location"]
    monkeypatch.setattr(get_settings(), "kakao_extra_scope", "account_email")
    response = client.get(f"{AUTH}/oauth/kakao/start", params={"return_to": FRONT})
    assert "scope=account_email" in response.headers["location"]


def test_ticket_is_bound_to_the_browser_that_started_the_login(
    client: TestClient, fake: FakeProvider
):
    """운영(웹과 API 가 같은 주소)에서는 표가 로그인을 시작한 브라우저의 쿠키와 맞아야 바뀐다.

    남이 자기 계정으로 받은 표를 링크로 보내 내 브라우저를 그 사람 계정에 로그인시키지 못하게.
    로컬 개발(localhost, 웹·API 포트가 다르다)은 묶지 않는다(위의 다른 시험들).
    """
    web = "https://talkto.example/"
    profile = _profile()
    state = _start(client, "kakao", return_to=web)
    code = f"code-{uuid4().hex}"
    fake.profiles[code] = profile
    response = client.get(
        f"{AUTH}/oauth/kakao/callback", params={"code": code, "state": state}
    )
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{web}#/auth?ticket={auth_module.BOUND_PREFIX}")
    ticket = location.split("ticket=", 1)[1]
    cookie = response.headers["set-cookie"].lower()
    assert f"{auth_module.TICKET_COOKIE}=" in cookie and "httponly" in cookie

    # 쿠키가 없는 다른 브라우저(공격자가 보낸 링크를 연 사람)는 바꾸지 못한다. 표는 그대로 남는다.
    saved = httpx.Cookies(client.cookies)
    client.cookies.clear()
    stolen = client.post(f"{AUTH}/oauth/exchange", json={"ticket": ticket})
    assert stolen.status_code == 401
    client.cookies = saved
    headers = _exchange(client, ticket)
    assert _me(client, headers)["name"] == "홍길동"
    # 한 번 쓴 표는 다시 못 쓴다
    assert client.post(f"{AUTH}/oauth/exchange", json={"ticket": ticket}).status_code == 401
