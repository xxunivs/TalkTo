# 살아있나 확인

from fastapi import APIRouter

from infra.schema.health import HealthResponse


router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="talkto-app-ai")
