from typing import Annotated

from fastapi import Depends

from infra.authentication import internal_token_header, verify_internal_token
from infra.setting import Settings, get_settings

SettingsDependency = Annotated[Settings, Depends(get_settings)]
InternalTokenDependency = Annotated[str | None, Depends(internal_token_header)]


def require_internal_request(
    settings: SettingsDependency,
    provided_token: InternalTokenDependency,
) -> None:
    verify_internal_token(settings, provided_token)
