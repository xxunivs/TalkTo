from typing import Annotated

from fastapi import Depends

from app.authentication import internal_token_header, verify_internal_token
from app.setting import Settings, get_settings

SettingsDependency = Annotated[Settings, Depends(get_settings)]
InternalTokenDependency = Annotated[str | None, Depends(internal_token_header)]


def require_internal_request(
    settings: SettingsDependency,
    provided_token: InternalTokenDependency,
) -> None:
    verify_internal_token(settings, provided_token)
