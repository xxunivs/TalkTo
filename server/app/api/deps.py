from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.models import User, UserRole, UserStatus
from app.db.session import get_db
from app.services import legal

bearer = HTTPBearer(auto_error=False)
DB = Annotated[AsyncSession, Depends(get_db)]


class ConsentRequired(Exception):
    """계정 필수 동의가 빠졌다. main.py 가 403 consent_required 로 바꾼다."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__("consent_required")
        self.missing = missing


async def get_authed_user(
    db: DB, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> User:
    """토큰만 확인한다(동의 게이트 앞). 동의 화면·로그아웃·탈퇴처럼 동의 전에도 열려야 하는 곳."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인한 뒤에 쓸 수 있어요.")
    try:
        payload = decode_token(credentials.credentials, "access")
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "로그인이 풀렸어요. 다시 로그인해 주세요."
        ) from exc
    user = await db.get(User, payload.get("sub"))
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "사용할 수 없는 계정이에요. 다시 로그인해 주세요."
        )
    if payload.get("ver") != user.refresh_token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그아웃됐어요. 다시 로그인해 주세요.")
    return user


AuthedUser = Annotated[User, Depends(get_authed_user)]


async def get_current_user(user: AuthedUser, db: DB) -> User:
    """로그인 + 계정 필수 동의(아홉 가지 모두)가 다 있는 사용자. 관리자는 동의 게이트를 거치지
    않는다."""
    if user.role != UserRole.ADMIN:
        missing = await legal.missing_required(db, user)
        if missing:
            raise ConsentRequired(missing)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "관리자만 쓸 수 있어요.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
