# 살아있나 확인

from fastapi import APIRouter

from infra.schema.health import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    # async: 스레드풀(40개) 미사용, OpenAI 지연으로 스레드가 다 차도 헬스체크 응답
    return HealthResponse(status="ok", service="talkto-personaai-ai")
