import logging
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit

import jwt
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DB, AuthedUser
from app.core import ratelimit
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_oauth_state,
    create_refresh_token,
    decode_oauth_state,
    decode_token,
    dummy_password_hash,
    hash_password,
    run_hashing,
    session_age_ok,
    verify_password,
)
from app.db.models import (
    SOCIAL_PLACEHOLDER_EMAIL_SUFFIX,
    CheckoutSession,
    Consent,
    LoginTicket,
    OwnerNotice,
    Person,
    SocialAccount,
    SubscriptionRequest,
    User,
    UserRole,
    UserStatus,
)
from app.schemas.api import (
    AccountDelete,
    AuthProviders,
    LoginRequest,
    MeView,
    OAuthTicketExchange,
    RefreshRequest,
    SignupRequest,
    TokenPair,
    UserView,
)
from app.schemas.common import APIModel, Envelope
from app.services import (
    analytics,
    billing,
    deletion,
    email_check,
    legal,
    mailer,
    persona_delete,
    sharing,
)
from app.services import oauth as oauth_service
from app.services.oauth import PROVIDER_LABELS, OAuthError, OAuthProfile, OAuthProvider

router = APIRouter(prefix="/auth", tags=["Auth"])
logger = logging.getLogger(__name__)

OAUTH_COOKIE = "talkto_oauth"  # 회사마다 talkto_oauth_kakao, talkto_oauth_google
OAUTH_COOKIE_MAX_AGE = 600
TICKET_TTL = timedelta(minutes=2)
# 로그인 표를 시작한 브라우저에 묶는 쿠키. 표는 주소(#/auth?ticket=…)로 웹에 건너가서, 묶지 않으면
# 남이 자기 계정으로 받은 표를 링크로 보내 내 브라우저를 그 사람 계정으로 로그인시킬 수 있다.
# 묶은 표는 앞에 BOUND_PREFIX 를 붙여 알아본다(표 줄에 칸을 더하지 않으려고).
TICKET_COOKIE = f"{OAUTH_COOKIE}_ticket"
BOUND_PREFIX = "b."
BAD_STATE = "로그인을 마치지 못했어요. 처음부터 다시 로그인해 주세요."
TICKET_EXPIRED = "로그인 시간이 지났어요. 다시 시도해 주세요."
ACCOUNT_UNAVAILABLE = "사용할 수 없는 계정이에요."
LOGGED_OUT = "로그인이 풀렸어요. 다시 로그인해 주세요."
LOCAL_HOSTS = ("localhost", "127.0.0.1")


def _tokens(user: User, auth_time: float | None = None) -> TokenPair:
    """새 토큰 한 쌍. auth_time(처음 로그인한 때)이 없으면 지금 로그인한 것이다."""
    return TokenPair(
        bearer_token=create_access_token(user.id, user.refresh_token_version),
        refresh_token=create_refresh_token(user.id, user.refresh_token_version, auth_time),
    )


async def user_views(db: AsyncSession, users: Sequence[User]) -> list[UserView]:
    """UserView 에 처음 이은 소셜 로그인을 붙인다. 여러 명이어도 한 번에 묻는다."""
    first: dict[str, str] = {}
    if users:
        rows = await db.execute(
            select(SocialAccount.user_id, SocialAccount.provider)
            .where(SocialAccount.user_id.in_([user.id for user in users]))
            .order_by(SocialAccount.created_at, SocialAccount.id)
        )
        for user_id, provider in rows:
            first.setdefault(user_id, provider)
    return [
        UserView.model_validate(user).model_copy(update={"provider": first.get(user.id)})
        for user in users
    ]


def _password_ok(password: str, hashed: str | None) -> bool:
    """비밀번호가 맞나. 없는 이메일(None)도 가짜 값과 견주어, 걸리는 시간으로 가입 여부를 모르게."""
    if hashed is None:
        verify_password(password, dummy_password_hash())
        return False
    return verify_password(password, hashed)


@router.post("/signup", response_model=Envelope[TokenPair], status_code=201)
async def signup(payload: SignupRequest, request: Request, db: DB) -> Envelope[TokenPair]:
    """누구나 가입한다. 막으려면 ALLOW_SIGNUP=false (그때는 관리자 초대로만 들어온다)."""
    ratelimit.SIGNUP_IP.check(ratelimit.client_ip(request))
    if not get_settings().allow_signup:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "지금은 가입을 받지 않아요.")
    email = payload.email.lower()
    # 메일을 받을 수 있는 주소인지(오타·버리는 메일·없는 도메인). 인증 메일은 보내지 않는다.
    if get_settings().email_domain_check:
        reason = await email_check.problem(email)
        if reason:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)
    if await db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 가입한 이메일이에요.")
    user = User(
        email=email,
        name=payload.name.strip(),
        # 비밀번호 해시는 오래 걸려(argon2) 다른 요청을 막지 않게 따로 돌린다
        password_hash=await run_hashing(hash_password, payload.password),
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        # 같은 이메일 가입이 동시에 두 번 들어왔다
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 가입한 이메일이에요.") from None
    # 가입 화면에서 체크한 계정 필수 동의(아홉 가지)를 같은 commit 에 남긴다(빠진 것은 첫 화면에서
    # 받는다)
    for consent_type in dict.fromkeys(payload.agreements):
        if consent_type in legal.ACCOUNT_TYPES:
            await legal.record(db, user, consent_type, True)
    await db.commit()
    await analytics.track(db, "signup", user_id=user.id, method="email", notify_email=user.email)
    if not await legal.missing_required(db, user):
        await analytics.track(db, "consent_accepted", user_id=user.id, at="signup")
    return Envelope(data=_tokens(user))


@router.post("/login", response_model=Envelope[TokenPair])
async def login(payload: LoginRequest, request: Request, db: DB) -> Envelope[TokenPair]:
    email = payload.email.lower()
    ip = ratelimit.client_ip(request)
    ratelimit.LOGIN_IP.check(ip)
    # 이메일별 횟수는 비밀번호를 보기 전에 센다(한꺼번에 몰아 보내도 넘지 못하게). 맞으면 되돌린다:
    # 제 비밀번호로 들어온 것은 실패 횟수에 넣지 않는다.
    email_key = f"{email}|{ip}"
    ratelimit.LOGIN_EMAIL.check(email_key)
    ratelimit.LOGIN_EMAIL_ANY_IP.check(email)
    user = await db.scalar(select(User).where(User.email == email))
    hashed = user.password_hash if user else None
    if not await run_hashing(_password_ok, payload.password, hashed):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "이메일이나 비밀번호가 맞지 않아요.")
    ratelimit.LOGIN_EMAIL.undo(email_key)
    ratelimit.LOGIN_EMAIL_ANY_IP.undo(email)
    if user.status == UserStatus.INVITED:
        # 관리자가 초대한 사람의 첫 로그인(받은 임시 비밀번호가 맞았다)
        user.status = UserStatus.ACTIVE
        await db.commit()
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, ACCOUNT_UNAVAILABLE)
    await analytics.track(db, "login", user_id=user.id, method="email")
    return Envelope(data=_tokens(user))


@router.post("/refresh", response_model=Envelope[TokenPair])
async def refresh(payload: RefreshRequest, request: Request, db: DB) -> Envelope[TokenPair]:
    ratelimit.REFRESH_IP.check(ratelimit.client_ip(request))
    try:
        token_payload = decode_token(payload.refresh_token, "refresh")
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LOGGED_OUT) from exc
    user = await db.get(User, token_payload.get("sub"))
    if (
        user is None
        or user.status != UserStatus.ACTIVE
        or token_payload.get("ver") != user.refresh_token_version
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LOGGED_OUT)
    # 처음 로그인한 때부터 max_session_days 가 지나면 다시 로그인한다(새어 나간 토큰을 끝없이
    # 갱신하지 못하게). 이 기능 전에 받은 토큰은 그 토큰을 받은 때부터 센다.
    auth_time = token_payload.get("auth_time", token_payload.get("iat"))
    if not isinstance(auth_time, int | float) or not session_age_ok(auth_time):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LOGGED_OUT)
    return Envelope(data=_tokens(user, auth_time))


@router.post("/logout", response_model=Envelope[dict])
async def logout(user: AuthedUser, db: DB) -> Envelope[dict]:
    user.refresh_token_version += 1
    await db.commit()
    return Envelope(data={"loggedOut": True})


class PasswordChange(APIModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/password", response_model=Envelope[TokenPair])
async def change_password(payload: PasswordChange, user: AuthedUser, db: DB) -> Envelope[TokenPair]:
    """비밀번호 바꾸기. 지금 비밀번호가 맞아야 한다.

    바꾸면 다른 기기는 모두 로그아웃된다(비밀번호가 새어 나갔을 때 막을 수 있게). 이 기기는 돌려준
    새 토큰으로 이어진다. 소셜 로그인으로만 가입한 분은 비밀번호가 없어 바꿀 수 없다.
    """
    ratelimit.PASSWORD_CHANGE.check(user.id)
    if not await run_hashing(_password_ok, payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "지금 비밀번호가 맞지 않아요.")
    user.password_hash = await run_hashing(hash_password, payload.new_password)
    user.refresh_token_version += 1
    await db.commit()
    return Envelope(data=_tokens(user))


@router.get("/me", response_model=Envelope[MeView])
async def me(user: AuthedUser, db: DB) -> Envelope[MeView]:
    """내 정보와 아직 받지 않은 계정 필수 동의(관리자는 받지 않는다)."""
    view = (await user_views(db, [user]))[0]
    needed = [] if user.role == UserRole.ADMIN else await legal.missing_required(db, user)
    return Envelope(data=MeView(**view.model_dump(), consents_needed=needed))


DELETE_WORD = "탈퇴"
DELETED_EMAIL_DOMAIN = "@deleted.talkto.invalid"


@router.delete("/me", response_model=Envelope[dict])
async def delete_account(payload: AccountDelete, user: AuthedUser, db: DB) -> Envelope[dict]:
    """회원 탈퇴. 모든 대화 상대를 지우고(구독도 끝난다) 계정은 알아볼 수 없게 바꾼다.

    결제 기록(5년 보존)·대화 횟수 기록·동의 이력은 남긴다. 그래서 계정 줄은 지우지 않는다.
    지운 일시는 대화 상대마다와 계정 한 줄로 남기고, 응답·메일에는 서버 백업에 남는 날짜를 함께
    알린다(deletion.py · SPEC §9). 앱 안 알림은 계정 줄이 남아 외래 키로 지워지지 않아 따로 지운다
    (계약 내용 알림도 이때 지운다).
    """
    if payload.confirm.strip() != DELETE_WORD:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'탈퇴'라고 적어 주세요.")
    if user.role == UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "관리자 계정은 여기서 탈퇴할 수 없어요.")
    # 주소를 알아볼 수 없게 바꾸기 전에 잡아 둔다(탈퇴 안내 메일)
    address = user.email
    personas = await persona_delete.owned_personas(db, user.id)
    persona_ids = [p.id for p in personas]
    # 하나라도 지울 수 없으면 아무것도 지우지 않는다
    for persona in personas:
        message = await persona_delete.blocker(db, persona)
        if message:
            raise HTTPException(status.HTTP_409_CONFLICT, message)
    claimed = []
    for persona in personas:
        message, sub = await persona_delete.claim(db, persona)
        if message:
            for held in claimed:
                await billing._release(db, held)
            raise HTTPException(status.HTTP_409_CONFLICT, message)
        if sub is not None:
            claimed.append(sub)
    removed = [await persona_delete.remove(db, persona) for persona in personas]
    # 대화 상대가 없는 사람 줄도 지운다
    for person in (await db.scalars(select(Person).where(Person.owner_user_id == user.id))).all():
        await db.delete(person)
    # 가족이 나눠 준 대화 상대에서 빠진다(거기서 나눈 내 대화·대화 음성도 지운다). 계정 줄은 남아
    # 외래 키로 지워지지 않으니 따로 한다. 동의 줄을 지우기 전에 한다(거둔 기록을 남긴다).
    await sharing.leave_all(db, user)

    user.email = f"deleted-{user.id}{DELETED_EMAIL_DOMAIN}"
    user.name = "탈퇴한 회원"
    user.password_hash = await run_hashing(hash_password, secrets.token_urlsafe(32))
    user.status = UserStatus.DELETED
    user.refresh_token_version += 1
    await db.execute(delete(SocialAccount).where(SocialAccount.user_id == user.id))
    await db.execute(delete(LoginTicket).where(LoginTicket.user_id == user.id))
    await db.execute(delete(Consent).where(Consent.user_id == user.id))
    # 구독 신청은 대화 상대와 함께 지워졌다. 혹시 남은 줄도 지운다.
    await db.execute(delete(SubscriptionRequest).where(SubscriptionRequest.user_id == user.id))
    await db.execute(delete(OwnerNotice).where(OwnerNotice.user_id == user.id))
    # 사용 통계는 남기되 누구의 것인지는 지운다
    await analytics.forget_user(db, user.id, persona_ids)
    await db.execute(
        update(CheckoutSession)
        .where(CheckoutSession.user_id == user.id, CheckoutSession.status == "OPEN")
        .values(status="EXPIRED")
    )
    await db.commit()
    result = await deletion.record(
        db,
        kind="account",
        user_id=user.id,
        persona_id=None,
        requested_by="owner",
        vendor_ok=deletion.combine(removed),
        detail={"personas": len(personas)},
    )
    mailer.send_user(
        address,
        "[TalkTo] 회원 탈퇴를 마쳤어요",
        f"{result.message}\n\n그동안 TalkTo와 함께해 주셔서 고마워요.",
    )
    await analytics.track(db, "account_deleted", personas=len(personas))
    return Envelope(data=deletion.response(result))


# ---- 소셜 로그인 (카카오·구글) ----
# 웹이 창 전체를 /start 로 옮긴다 → 그 회사 로그인 → /callback 이 사용자를 찾거나 만들고
# 한 번짜리 표를 붙여 웹으로 돌려보낸다 → 웹이 /oauth/exchange 로 표를 토큰과 바꾼다.


@router.get("/providers", response_model=Envelope[AuthProviders])
async def providers() -> Envelope[AuthProviders]:
    """로그인 화면이 버튼을 켤지 정한다. 키를 넣지 않은 로그인은 false."""
    return Envelope(
        data=AuthProviders(
            kakao=oauth_service.is_configured(OAuthProvider.KAKAO),
            google=oauth_service.is_configured(OAuthProvider.GOOGLE),
            signup=get_settings().allow_signup,
        )
    )


def _origin(url: str) -> str | None:
    """scheme://host[:port] 로 맞춘다(기본 포트는 뺀다). 이상한 주소면 None."""
    # 브라우저마다 공백·역슬래시를 다르게 읽어 다른 곳으로 새는 일이 있어 아예 받지 않는다.
    if any(ord(ch) <= 0x20 or ch in "\\\x7f" for ch in url):
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or "@" in parts.netloc or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    default_port = 80 if parts.scheme == "http" else 443
    suffix = f":{port}" if port and port != default_port else ""
    return f"{parts.scheme}://{parts.hostname}{suffix}"


def _safe_return_to(url: str) -> str | None:
    """돌아갈 주소가 우리 웹인지 본다. 아무 데나 돌려보내면 표를 남에게 넘겨주게 된다."""
    url = url.split("#", 1)[0]
    origin = _origin(url)
    if origin is None:
        return None
    settings = get_settings()
    allowed = {_origin(item) for item in [*settings.cors_origins, settings.frontend_url]}
    if origin in allowed:
        return url
    # 로컬은 flutter run 포트가 매번 바뀌어 CORS 처럼 localhost 를 다 받는다.
    parts = urlsplit(url)
    if settings.cors_allow_localhost and parts.scheme == "http" and parts.hostname in LOCAL_HOSTS:
        return url
    return None


def _redirect_uri(provider: OAuthProvider) -> str:
    """그 회사 개발자 콘솔에 똑같이 등록해 둬야 하는 주소."""
    settings = get_settings()
    base = settings.public_base_url.rstrip("/")
    return f"{base}{settings.api_v1_prefix}/auth/oauth/{provider}/callback"


def _cookie_name(provider: str) -> str:
    """회사마다 이름을 따로 둔다. 카카오·구글 로그인을 번갈아 시작해도 서로 덮어쓰지 않게.

    이름이 같고 경로만 다르면 브라우저가 둘 다 보내고 서버는 그중 하나만 읽어서 어긋난다.
    """
    return f"{OAUTH_COOKIE}_{provider}"


def _cookie_options() -> dict:
    settings = get_settings()
    return {
        "path": f"{settings.api_v1_prefix}/auth/oauth",
        "httponly": True,
        "samesite": "lax",
        "secure": settings.public_base_url.startswith("https://"),
    }


def _bind_ticket(return_to: str) -> bool:
    """로그인 표를 이 브라우저(쿠키)에 묶을지.

    운영은 웹과 API 가 같은 주소라 교환 요청에 쿠키가 실린다. 로컬 개발(웹·API 포트가 달라 쿠키가
    실리지 않는다)만 묶지 않는다. 운영은 localhost 로 돌아가는 로그인을 받지 않는다.
    """
    settings = get_settings()
    return not (settings.cors_allow_localhost and urlsplit(return_to).hostname in LOCAL_HOSTS)


def _not_open(label: str) -> str:
    """키를 넣지 않은 소셜 로그인."""
    return f"{label} 로그인은 아직 쓸 수 없어요. 다른 방법으로 로그인해 주세요."


def _back_with_error(return_to: str, message: str, provider: str) -> RedirectResponse:
    """로그인 화면으로 돌려보내며 보여줄 말을 붙인다."""
    response = RedirectResponse(f"{return_to}#/login?error={quote(message, safe='')}", 302)
    response.delete_cookie(_cookie_name(provider), **_cookie_options())
    return response


@router.get("/oauth/{provider}/start", response_class=RedirectResponse)
async def oauth_start(
    provider: OAuthProvider,
    return_to: str | None = Query(None, max_length=2000),
) -> RedirectResponse:
    """로그인 버튼이 창 전체를 이 주소로 옮긴다. 끝나면 return_to 로 돌아간다."""
    safe_return_to = _safe_return_to(return_to or get_settings().frontend_url)
    if safe_return_to is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "이 화면에서는 로그인할 수 없어요. TalkTo 화면에서 다시 해 주세요.",
        )
    if not oauth_service.is_configured(provider):
        return _back_with_error(safe_return_to, _not_open(PROVIDER_LABELS[provider]), provider)
    nonce = secrets.token_urlsafe(24)
    state = create_oauth_state(provider, safe_return_to, nonce)
    response = RedirectResponse(
        oauth_service.authorize_url(provider, _redirect_uri(provider), state), 302
    )
    response.set_cookie(
        _cookie_name(provider), nonce, max_age=OAUTH_COOKIE_MAX_AGE, **_cookie_options()
    )
    # 예전 이름(talkto_oauth)으로 남은 쿠키를 지운다. 그대로 두면 같은 이름이 둘 보내져 어긋났다.
    options = _cookie_options()
    response.delete_cookie(OAUTH_COOKIE, **options)
    response.delete_cookie(OAUTH_COOKIE, **(options | {"path": f"{options['path']}/{provider}"}))
    return response


class _LoginRefused(Exception):
    """콜백에서 로그인 화면으로 돌려보낼 까닭. 메시지는 그대로 화면에 보인다."""


async def _social_user(db: AsyncSession, profile: OAuthProfile) -> tuple[User, bool]:
    """이은 계정 → 같은 인증 이메일의 기존 사용자 → 새 사용자 순으로 찾는다.

    (사용자, 새로 가입했나) — 새로 가입했나는 통계의 가입/로그인 구분에 쓴다.
    """
    account = await db.scalar(
        select(SocialAccount).where(
            SocialAccount.provider == profile.provider,
            SocialAccount.provider_user_id == profile.subject,
        )
    )
    if account is not None:
        user = await db.get(User, account.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise _LoginRefused(ACCOUNT_UNAVAILABLE)
        if profile.email and account.email != profile.email:
            account.email = profile.email
        # 예전에 이메일 없이 가입한 사람(카카오 비즈 앱 전)이 이제 인증된 이메일을 주면
        # 그것으로 바꾼다
        fresh = profile.email.strip().lower() if profile.email and profile.email_verified else None
        if (
            fresh
            and user.email.endswith(SOCIAL_PLACEHOLDER_EMAIL_SUFFIX)
            and not await db.scalar(select(User.id).where(User.email == fresh))
        ):
            user.email = fresh
        return user, False

    # 그 회사가 인증한 이메일만 믿는다. 아니면 남의 이메일로 그 사람 계정에 들어갈 수 있다.
    email = profile.email.strip().lower() if profile.email and profile.email_verified else None
    user = await db.scalar(select(User).where(User.email == email)) if email else None
    if user is not None and not await db.scalar(
        select(SocialAccount.id).where(SocialAccount.user_id == user.id).limit(1)
    ):
        # 이메일·비밀번호 가입은 이메일을 확인하지 않는다. 남이 내 이메일로 먼저 가입해 둔 계정에
        # 이어 붙으면 그 사람이 내 대화를 보게 되니, 소셜로 확인된 적 있는 계정에만 잇는다.
        user, email = None, None
    if user is not None and user.status != UserStatus.ACTIVE:
        raise _LoginRefused(ACCOUNT_UNAVAILABLE)
    created = user is None
    if user is None:
        if not get_settings().allow_signup:
            raise _LoginRefused("지금은 가입을 받지 않아요.")
        user = User(
            email=email or f"{profile.provider}_{profile.subject}{SOCIAL_PLACEHOLDER_EMAIL_SUFFIX}",
            name=(profile.name or "").strip()[:100] or "사용자",
            # 비밀번호 로그인은 쓰지 않는다. 아무도 모르는 값으로 막아 둔다.
            password_hash=await run_hashing(hash_password, secrets.token_urlsafe(32)),
            role=UserRole.USER,
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        await db.flush()
    db.add(
        SocialAccount(
            user_id=user.id,
            provider=profile.provider,
            provider_user_id=profile.subject,
            email=profile.email,
        )
    )
    return user, created


@router.get("/oauth/{provider}/callback", response_class=RedirectResponse)
async def oauth_callback(
    provider: OAuthProvider,
    request: Request,
    db: DB,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """그 회사가 로그인 뒤 돌려보내는 곳. 성공하면 웹 #/auth?ticket=… 으로 보낸다."""
    try:
        claims = decode_oauth_state(state or "")
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, BAD_STATE) from exc
    return_to = _safe_return_to(claims["return_to"])
    if claims["provider"] != provider or return_to is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, BAD_STATE)

    label = PROVIDER_LABELS[provider]
    failed = f"{label} 로그인에 실패했어요. 잠시 후 다시 시도해 주세요."
    if error:
        cancelled = error == "access_denied"
        return _back_with_error(
            return_to, "로그인을 취소했어요." if cancelled else failed, provider
        )
    # 이 브라우저에서 시작한 로그인인지 본다. 남의 code 를 끼워 넣는 로그인 위조를 막는다.
    nonce = request.cookies.get(_cookie_name(provider)) or ""
    if not secrets.compare_digest(nonce.encode(), claims["nonce"].encode()):
        return _back_with_error(return_to, BAD_STATE, provider)
    if not oauth_service.is_configured(provider):
        return _back_with_error(return_to, _not_open(label), provider)
    if not code:
        return _back_with_error(return_to, failed, provider)
    try:
        profile = await oauth_service.fetch_profile(provider, code, _redirect_uri(provider))
    except OAuthError as exc:
        logger.warning("social login failed: %s", exc)
        return _back_with_error(return_to, failed, provider)

    bound = _bind_ticket(return_to)
    try:
        user, created = await _social_user(db, profile)
        ticket = LoginTicket(
            id=(BOUND_PREFIX if bound else "") + secrets.token_urlsafe(32),
            user_id=user.id,
            expires_at=datetime.now(UTC) + TICKET_TTL,
        )
        db.add(ticket)
        await db.commit()
    except _LoginRefused as exc:
        return _back_with_error(return_to, str(exc), provider)
    except IntegrityError:
        # 같은 계정으로 동시에 두 번 들어와 연결이 겹쳤다. 다시 하면 이어진 계정으로 들어간다.
        await db.rollback()
        return _back_with_error(return_to, "잠시 후 다시 시도해 주세요.", provider)
    await analytics.track(
        db,
        "signup" if created else "login",
        user_id=user.id,
        method=str(provider),
        notify_email=user.email if created else None,
    )

    response = RedirectResponse(f"{return_to}#/auth?ticket={ticket.id}", 302)
    response.delete_cookie(_cookie_name(provider), **_cookie_options())
    if bound:
        response.set_cookie(
            TICKET_COOKIE,
            ticket.id,
            max_age=int(TICKET_TTL.total_seconds()),
            **_cookie_options(),
        )
    return response


@router.post("/oauth/exchange", response_model=Envelope[TokenPair])
async def oauth_exchange(
    payload: OAuthTicketExchange, request: Request, response: Response, db: DB
) -> Envelope[TokenPair]:
    """소셜 로그인 표를 토큰으로 바꾼다. 표는 지우면서 읽어 두 번 쓰지 못한다.

    묶은 표(BOUND_PREFIX)는 로그인을 시작한 브라우저의 쿠키와 같아야 한다(남이 받은 표로 내
    브라우저를 그 사람 계정에 로그인시키지 못하게).
    """
    ratelimit.OAUTH_EXCHANGE_IP.check(ratelimit.client_ip(request))
    if payload.ticket.startswith(BOUND_PREFIX):
        held = request.cookies.get(TICKET_COOKIE) or ""
        if not secrets.compare_digest(held.encode(), payload.ticket.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, TICKET_EXPIRED)
    response.delete_cookie(TICKET_COOKIE, **_cookie_options())
    now = datetime.now(UTC)
    user_id = await db.scalar(
        delete(LoginTicket)
        .where(LoginTicket.id == payload.ticket, LoginTicket.expires_at > now)
        .returning(LoginTicket.user_id)
        .execution_options(synchronize_session=False)
    )
    # 쓰지 않고 지난 표도 이참에 치운다.
    await db.execute(
        delete(LoginTicket)
        .where(LoginTicket.expires_at <= now)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    user = await db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, TICKET_EXPIRED)
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, ACCOUNT_UNAVAILABLE)
    return Envelope(data=_tokens(user))
