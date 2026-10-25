from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.routes import analysis, embeddings, health, persona, voice
from app.error import (
    AppError,
    app_error_handler,
    internal_validation_error_handler,
    provider_bad_request_handler,
    provider_error_handler,
    validation_error_handler,
)
from app.log import configure_logging
from app.setting import get_settings
from app.temp_files import reset_scratch_dir


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    deployed = settings.deployed
    # 배포 환경은 입장 토큰 없으면 기동 거부 (인증 없는 노출 방지)
    if deployed and settings.ai_server_token is None:
        raise RuntimeError("AI_SERVER_TOKEN is required when APP_ENV is staging or production.")
    if deployed:
        # OOM 강제 종료로 지난 프로세스가 못 지운 녹음, PCM, 목소리 재료 삭제 (app/temp_files.py)
        # 로컬 제외 (같은 PC 개발 서버 파일 보호)
        reset_scratch_dir(settings)
    # 배포 환경은 API 문서(/docs, /redoc, /openapi.json) 비공개
    docs = {"docs_url": None, "redoc_url": None, "openapi_url": None} if deployed else {}

    application = FastAPI(
        title="TalkTo APP AI",
        description="Internal AI service and worker API for the TalkTo app",
        version="0.1.0",
        **docs,
    )
    application.add_exception_handler(AppError, app_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    # 내부 생성 값의 스키마 위반 -> 422 + 코드 (받은 글 로그 미기록)
    application.add_exception_handler(ValidationError, internal_validation_error_handler)
    # OpenAI 크레딧, 키, 요청 한도, 시간 초과, 연결 실패, 5xx -> 503 + 코드 (BE 관리자 화면 알림)
    import openai

    for kind in (
        openai.RateLimitError,
        openai.AuthenticationError,
        openai.PermissionDeniedError,
        openai.APIConnectionError,  # APITimeoutError 포함
        openai.InternalServerError,
    ):
        application.add_exception_handler(kind, provider_error_handler)
    # OpenAI 요청 거부(400, 글 길이 초과 등) -> 재시도 무의미라 422 + 코드
    application.add_exception_handler(openai.BadRequestError, provider_bad_request_handler)
    application.include_router(health.router)
    application.include_router(persona.router, prefix="/v1/persona")
    application.include_router(analysis.router, prefix="/v1/analysis")
    application.include_router(embeddings.router, prefix="/v1")
    application.include_router(voice.router, prefix="/v1/voice")
    if settings.persona_orchestrator:
        # 기동 시 중간계층 생성 (형태소 분석기 등 무거운 준비를 첫 요청 전에)
        from orchestrator.service import get_orchestrator

        get_orchestrator(settings)
    return application


app = create_app()
