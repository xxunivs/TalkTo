import asyncio
import secrets
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


# argon2 는 한 번에 64MB 쯤 쓴다. 전용 스레드 둘에서만 돌린다:
# 가입·로그인이 몰려도 메모리가 튀지 않고,
# ffmpeg 처럼 오래 걸리는 작업이 기본 스레드를 다 차지해도 로그인은 멈추지 않는다.
HASH_SLOTS = 2
_hash_pool = ThreadPoolExecutor(max_workers=HASH_SLOTS, thread_name_prefix="password-hash")


async def run_hashing[T](fn: Callable[..., T], *args) -> T:
    """비밀번호 해시·확인을 전용 스레드에서 돌린다."""
    return await asyncio.get_running_loop().run_in_executor(_hash_pool, fn, *args)


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    """없는 이메일로 로그인할 때 견줄 값. 처음 쓸 때 한 번 만든다."""
    return hash_password(secrets.token_urlsafe(16))


def create_token(
    subject: str,
    token_type: str,
    token_version: int,
    expires_delta: timedelta,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "ver": token_version,
        "iat": now,
        "exp": now + expires_delta,
        **(extra or {}),
    }
    settings = get_settings()
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def session_age_ok(auth_time: float, now: datetime | None = None) -> bool:
    """처음 로그인한 때(auth_time, epoch 초)부터 max_session_days 가 지나지 않았나."""
    current = (now or datetime.now(UTC)).timestamp()
    return current - auth_time <= get_settings().max_session_days * 86400


def create_access_token(subject: str, token_version: int) -> str:
    settings = get_settings()
    return create_token(
        subject,
        "access",
        token_version,
        timedelta(minutes=settings.access_token_ttl_minutes),
    )


def create_refresh_token(subject: str, token_version: int, auth_time: float | None = None) -> str:
    """갱신 토큰. auth_time 은 처음 로그인한 때(epoch 초)로, 새로 받아도 그대로 이어 간다.

    처음 로그인한 때부터 max_session_days 가 지나면 더 갱신하지 않는다(새어 나간 토큰을 끝없이
    갱신해 쓰지 못하게). 만료도 그때를 넘지 않는다.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    started = int(now.timestamp() if auth_time is None else auth_time)
    left = timedelta(seconds=started + settings.max_session_days * 86400 - now.timestamp())
    ttl = min(timedelta(days=settings.refresh_token_ttl_days), max(left, timedelta(minutes=1)))
    return create_token(subject, "refresh", token_version, ttl, {"auth_time": started})


def create_oauth_state(provider: str, return_to: str, nonce: str) -> str:
    """소셜 로그인 왕복 동안 들고 다니는 state. 서명해 두어 돌아갈 주소를 중간에 못 바꾼다.

    nonce 는 같은 값을 쿠키에도 두어, 남이 시작한 로그인을 내 브라우저에 끼워 넣지 못하게 한다.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "type": "oauth_state",
        "provider": provider,
        "return_to": return_to,
        "nonce": nonce,
        "iat": now,
        "exp": now + timedelta(minutes=10),
    }
    settings = get_settings()
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_oauth_state(state: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(
        state,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp"]},
    )
    if payload.get("type") != "oauth_state":
        raise jwt.InvalidTokenError("Unexpected token type")
    if not all(isinstance(payload.get(key), str) for key in ("provider", "return_to", "nonce")):
        raise jwt.InvalidTokenError("Malformed oauth state")
    return payload


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Unexpected token type")
    return payload
