import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    ai_server_token: SecretStr | None = None

    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-4.1-mini"
    openai_analysis_model: str = "gpt-4.1-mini"
    openai_stt_model: str = "whisper-1"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = Field(default=1536, gt=0)
    openai_timeout_seconds: float = Field(default=60, gt=0)
    openai_max_retries: int = Field(default=2, ge=0)

    tts_provider: str = "elevenlabs"
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_model: str | None = None
    elevenlabs_default_voice_id: str | None = None

    max_audio_bytes: int | None = Field(default=None, gt=0)
    temp_dir: Path | None = None

    @field_validator(
        "ai_server_token",
        "openai_api_key",
        "elevenlabs_api_key",
        "elevenlabs_model",
        "elevenlabs_default_voice_id",
        "max_audio_bytes",
        "temp_dir",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return normalized


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)

    return Settings(
        app_env=os.getenv("APP_ENV", "local"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        ai_server_token=os.getenv("AI_SERVER_TOKEN"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_chat_model=os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-mini"),
        openai_analysis_model=os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-4.1-mini"),
        openai_stt_model=os.getenv("OPENAI_STT_MODEL", "whisper-1"),
        openai_embedding_model=os.getenv(
            "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
        ),
        openai_embedding_dimensions=os.getenv(
            "OPENAI_EMBEDDING_DIMENSIONS", "1536"
        ),
        openai_timeout_seconds=os.getenv("OPENAI_TIMEOUT_SECONDS", "60"),
        openai_max_retries=os.getenv("OPENAI_MAX_RETRIES", "2"),
        tts_provider=os.getenv("TTS_PROVIDER", "elevenlabs"),
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY"),
        elevenlabs_model=os.getenv("ELEVENLABS_MODEL"),
        elevenlabs_default_voice_id=os.getenv("ELEVENLABS_DEFAULT_VOICE_ID"),
        max_audio_bytes=os.getenv("MAX_AUDIO_BYTES"),
        temp_dir=os.getenv("TEMP_DIR"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
