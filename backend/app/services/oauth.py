"""카카오·구글 로그인과 주고받는 HTTP. 받은 code 를 토큰으로 바꾸고 프로필을 읽는다.

회사마다 모양이 달라서 여기서 OAuthProfile 하나로 맞춘다. 테스트는 fetch_profile 만 바꿔 끼운다.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.core.config import get_settings

TIMEOUT_SECONDS = 10

KAKAO_AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_PROFILE_URL = "https://kapi.kakao.com/v2/user/me"
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_PROFILE_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class OAuthProvider(StrEnum):
    KAKAO = "kakao"
    GOOGLE = "google"


PROVIDER_LABELS = {OAuthProvider.KAKAO: "카카오", OAuthProvider.GOOGLE: "구글"}


class OAuthError(RuntimeError):
    """그 회사와 주고받다 실패했다. 메시지는 로그용이고 화면에는 따로 정한 말을 보인다."""


@dataclass(frozen=True)
class OAuthProfile:
    provider: str
    subject: str  # 카카오 id, 구글 sub. 바뀌지 않는다.
    email: str | None
    email_verified: bool
    name: str | None


def credentials(provider: OAuthProvider) -> tuple[str, str] | None:
    """(client id, secret). 둘 중 하나라도 비면 그 로그인은 꺼진 것으로 본다."""
    settings = get_settings()
    if provider == OAuthProvider.KAKAO:
        pair = (settings.kakao_client_id, settings.kakao_client_secret)
    else:
        pair = (settings.google_client_id, settings.google_client_secret)
    client_id, secret = (value.strip() if value else "" for value in pair)
    return (client_id, secret) if client_id and secret else None


def is_configured(provider: OAuthProvider) -> bool:
    return credentials(provider) is not None


def authorize_url(provider: OAuthProvider, redirect_uri: str, state: str) -> str:
    client_id, _ = credentials(provider) or ("", "")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
    }
    if provider == OAuthProvider.KAKAO:
        params["state"] = state
        extra = (get_settings().kakao_extra_scope or "").strip()
        if extra:
            params["scope"] = extra
        return f"{KAKAO_AUTHORIZE_URL}?{urlencode(params, quote_via=quote)}"
    # 여러 구글 계정을 쓰는 사람이 많아 매번 계정을 고르게 한다.
    params |= {"scope": "openid email profile", "state": state, "prompt": "select_account"}
    return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params, quote_via=quote)}"


async def fetch_profile(provider: OAuthProvider, code: str, redirect_uri: str) -> OAuthProfile:
    if provider == OAuthProvider.KAKAO:
        return await kakao_profile(code, redirect_uri)
    return await google_profile(code, redirect_uri)


async def kakao_profile(code: str, redirect_uri: str) -> OAuthProfile:
    data = await _exchange_and_read(
        OAuthProvider.KAKAO, KAKAO_TOKEN_URL, KAKAO_PROFILE_URL, code, redirect_uri
    )
    if data.get("id") is None:
        raise OAuthError("kakao profile has no id")
    account = data.get("kakao_account") or {}
    profile = account.get("profile") or {}
    properties = data.get("properties") or {}
    # 카카오는 인증한 이메일이어도 지금은 못 쓰는 주소(is_email_valid=false)일 수 있어 둘 다 본다.
    verified = account.get("is_email_verified") is True and account.get("is_email_valid") is True
    return OAuthProfile(
        provider=OAuthProvider.KAKAO,
        subject=str(data["id"]),
        email=account.get("email") or None,
        email_verified=verified,
        name=profile.get("nickname") or properties.get("nickname") or None,
    )


async def google_profile(code: str, redirect_uri: str) -> OAuthProfile:
    data = await _exchange_and_read(
        OAuthProvider.GOOGLE, GOOGLE_TOKEN_URL, GOOGLE_PROFILE_URL, code, redirect_uri
    )
    if not data.get("sub"):
        raise OAuthError("google profile has no sub")
    return OAuthProfile(
        provider=OAuthProvider.GOOGLE,
        subject=str(data["sub"]),
        email=data.get("email") or None,
        email_verified=data.get("email_verified") in (True, "true"),
        name=data.get("name") or None,
    )


async def _exchange_and_read(
    provider: OAuthProvider, token_url: str, profile_url: str, code: str, redirect_uri: str
) -> dict[str, Any]:
    """code → access token → 프로필 JSON. 두 회사 모두 순서가 같다."""
    pair = credentials(provider)
    if pair is None:
        raise OAuthError(f"{provider} is not configured")
    client_id, secret = pair
    form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            token_response = await client.post(token_url, data=form)
            if token_response.status_code >= 400:
                raise OAuthError(f"{provider} token exchange failed: {token_response.status_code}")
            token = token_response.json()
            access_token = token.get("access_token") if isinstance(token, dict) else None
            if not access_token:
                raise OAuthError(f"{provider} token response has no access_token")
            profile_response = await client.get(
                profile_url, headers={"Authorization": f"Bearer {access_token}"}
            )
            if profile_response.status_code >= 400:
                raise OAuthError(f"{provider} profile failed: {profile_response.status_code}")
            data = profile_response.json()
    except httpx.HTTPError as exc:
        raise OAuthError(f"{provider} request failed: {type(exc).__name__}") from exc
    except ValueError as exc:  # JSON 이 아닌 답
        raise OAuthError(f"{provider} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise OAuthError(f"{provider} returned unexpected profile")
    return data
