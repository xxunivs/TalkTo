from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from infra.be_ai_api.routes import analysis, embeddings, health, persona, voice
from infra.setting import get_settings
from infra.error import (
    AppError,
    app_error_handler,
    internal_validation_error_handler,
    provider_bad_request_handler,
    provider_error_handler,
    validation_error_handler,
)
from infra.log import configure_logging
from infra.scratch import reset_scratch_dir


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    deployed = settings.deployed
    # 배포 환경은 입장 토큰이 없으면 아예 뜨지 않는다(인증 없이 열리지 않게)
    if deployed and settings.ai_server_token is None:
        raise RuntimeError("AI_SERVER_TOKEN is required when APP_ENV is staging or production.")
    if deployed:
        # 강제 종료(OOM)로 지난 프로세스가 못 지운 녹음·PCM·목소리 재료를 지운다(infra/scratch.py).
        # 로컬은 지우지 않는다(같은 PC 에서 돌고 있는 개발 서버의 파일을 테스트가 지우지 않게).
        reset_scratch_dir(settings)
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
    # 처리 중에 만든 값이 스키마를 어기면 422 + 코드(받은 글을 로그에 남기지 않는다)
    application.add_exception_handler(ValidationError, internal_validation_error_handler)
    # OpenAI 크레딧·키·요청 한도·시간 초과·연결 실패·5xx 는 503 + 코드로(백엔드가 관리자 화면에 알린다)
    import openai

    for kind in (
        openai.RateLimitError,
        openai.AuthenticationError,
        openai.PermissionDeniedError,
        openai.APIConnectionError,  # APITimeoutError 도 여기에 든다
        openai.InternalServerError,
    ):
        application.add_exception_handler(kind, provider_error_handler)
    # OpenAI 가 요청을 받지 않음(400, 글이 너무 김 등). 다시 해도 같아서 422 + 코드
    application.add_exception_handler(openai.BadRequestError, provider_bad_request_handler)
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
