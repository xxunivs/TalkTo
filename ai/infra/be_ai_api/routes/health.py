# 살아있나 확인

from fastapi import APIRouter

from infra.schema.health import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    # async: 스레드풀(40개)을 쓰지 않는다. OpenAI 가 느려 대답 요청이 스레드를 다 잡아도 헬스체크는 답한다.
    return HealthResponse(status="ok", service="talkto-personaai-ai")
