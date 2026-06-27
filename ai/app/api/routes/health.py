from fastapi import APIRouter

from app.api.dependencies import SettingsDependency
from app.schemas.common import HealthResponse, ReadinessCheck, ReadyResponse


router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="talkto-app-ai")


@router.get("/ready", response_model=ReadyResponse)
def ready(settings: SettingsDependency) -> ReadyResponse:
    auth_status = (
        "configured" if settings.ai_server_token is not None else "disabled_local"
    )
    return ReadyResponse(
        status="ready",
        checks={
            "configuration": ReadinessCheck(status="ok"),
            "internal_auth": ReadinessCheck(status=auth_status),
            "openai": ReadinessCheck(status="not_checked"),
            "tts": ReadinessCheck(status="not_checked"),
        },
    )
