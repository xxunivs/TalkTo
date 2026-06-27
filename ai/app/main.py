from fastapi import FastAPI

from app.api.routes import analysis, embeddings, health, persona, voice
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(
        title="TalkTo APP AI",
        description="Internal AI service and worker API for the TalkTo app",
        version="0.1.0",
    )
    application.add_exception_handler(AppError, app_error_handler)
    application.include_router(health.router)
    application.include_router(persona.router, prefix="/v1/persona")
    application.include_router(analysis.router, prefix="/v1/analysis")
    application.include_router(embeddings.router, prefix="/v1")
    application.include_router(voice.router, prefix="/v1/voice")
    return application


app = create_app()
