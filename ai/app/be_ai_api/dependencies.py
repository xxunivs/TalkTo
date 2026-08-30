from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.core.security import internal_token_header, verify_internal_token


SettingsDependency = Annotated[Settings, Depends(get_settings)]
InternalTokenDependency = Annotated[str | None, Depends(internal_token_header)]


def require_internal_request(
    settings: SettingsDependency,
    provided_token: InternalTokenDependency,
) -> None:
    verify_internal_token(settings, provided_token)
