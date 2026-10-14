from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from infra.be_ai_api.routes import analysis, embeddings, health, persona, voice
from infra.setting import get_settings
from infra.error import AppError, app_error_handler, provider_error_handler, validation_error_handler
from infra.log import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    deployed = settings.deployed
    # 배포 환경은 입장 토큰이 없으면 아예 뜨지 않는다(인증 없이 열리지 않게)
    if deployed and settings.ai_server_token is None:
        raise RuntimeError("AI_SERVER_TOKEN is required when APP_ENV is staging or production.")
    # 배포 환경에서는 API 문서(/docs·/redoc·/openapi.json)를 닫는다
    docs = {"docs_url": None, "redoc_url": None, "openapi_url": None} if deployed else {}

    application = FastAPI(
        title="TalkTo APP AI",
        description="Internal AI service and worker API for the TalkTo app",
        version="0.1.0",
        **docs,
    )
    application.add_exception_handler(AppError, app_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    # OpenAI 크레딧·키·요청 한도 문제는 503 + 코드로(백엔드가 관리자 화면에 알린다)
    import openai

    for kind in (openai.RateLimitError, openai.AuthenticationError, openai.PermissionDeniedError):
        application.add_exception_handler(kind, provider_error_handler)
    application.include_router(health.router)
    application.include_router(persona.router, prefix="/v1/persona")
    application.include_router(analysis.router, prefix="/v1/analysis")
    application.include_router(embeddings.router, prefix="/v1")
    application.include_router(voice.router, prefix="/v1/voice")
    if settings.persona_orchestrator:
        # 중간계층을 시작할 때 만든다(형태소 분석기 등 무거운 준비를 첫 요청 전에)
        from orchestrator.service import get_orchestrator
        get_orchestrator(settings)
    return application


app = create_app()
