# 키, 모델명, 숫자 설정

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
    # 대화·분석·교정은 gpt-6-luna 하나로 쓴다(2026-10 기준 최신 세대 중 가장 싸다:
    # 입력 $0.10 / 출력 $0.50, 1M 토큰). 대화 1턴 1.1초로 gpt-4.1-mini(3.7초)보다 빠르다.
    openai_chat_model: str = "gpt-6-luna"
    openai_analysis_model: str = "gpt-6-luna"
    # 평가 채점(LLM judge)용 상위 모델 — 채점 대상 모델보다 강한 모델 사용 원칙
    openai_judge_model: str = "gpt-5.5"
    openai_stt_model: str = "whisper-1"
    openai_correction_model: str = "gpt-6-luna"
    # 교정 발음 유사도 게이트(자모 편집거리 비율 상한). 초기값은 실통화 13건
    # 캘리브레이션 — 외부 데이터 일반화 테스트에서 재조정 대상
    correction_max_jamo_ratio: float = Field(default=0.45, gt=0, le=1)
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = Field(default=1536, gt=0)
    openai_timeout_seconds: float = Field(default=60, gt=0)
    openai_max_retries: int = Field(default=2, ge=0)

    tts_provider: str = "elevenlabs"
    elevenlabs_api_key: SecretStr | None = None
    # 기본은 v4 Turbo: 목소리가 조금 달라도 감정 표현이 좋고 AI 느낌이 덜하다(2026-10 대표 청취).
    # v4 학습이 안 된 PVC 는 provider 가 v2 로 되돌린다.
    elevenlabs_model: str | None = "eleven_v4_turbo"
    elevenlabs_default_voice_id: str | None = None
    # 말하는 속도(ElevenLabs speed). 1.0=기본, <1 느리게(대표 피드백: 조금 느리게).
    # 0.7~1.2 권장. 요청에서 speed로 오버라이드 가능.
    elevenlabs_speed: float = Field(default=1.0, ge=0.7, le=1.2)
    # 목소리 결. 대상자마다 달라야 해서 요청에서 덮어쓸 수 있다. 여기 값은 기본값이다.
    # 흔들림을 조금 허용해 표현을 살리고(0.3), 원본 음색은 유지하고(0.75),
    # 과장 스타일은 끈다(0.0).
    elevenlabs_stability: float = Field(default=0.3, ge=0.0, le=1.0)
    elevenlabs_similarity_boost: float = Field(default=0.75, ge=0.0, le=1.0)
    elevenlabs_style: float = Field(default=0.0, ge=0.0, le=1.0)
    # MiniMax: 새로 만드는 목소리를 TTS_PROVIDER=minimax 로 둘 때. 합성은 voice_id 로
    # 어느 회사 목소리인지 보고 보낸다(이미 만든 ElevenLabs 목소리도 계속 쓴다).
    minimax_api_key: SecretStr | None = None
    # turbo: 대화는 첫 소리가 빨라야 해서. hd 는 더 곱지만 느리다.
    minimax_model: str = "speech-2.8-turbo"
    # 비우면 문장에 맞춰 MiniMax 가 고른다(happy, sad, calm ...).
    minimax_emotion: str | None = None
    # 목소리 결: +면 부드럽게, -면 힘 있게(-100~100). 0 이면 보내지 않는다.
    minimax_intensity: int = Field(default=0, ge=-100, le=100)
    # 대답 음성 감정: off = 목소리 회사가 글을 보고 정함(기존 동작), inline = 대답 LLM 이
    # 같은 호출에서 감정을 하나 골라 붙인다(pipeline/s4_voice/b_emotion.py).
    voice_emotion: Literal["off", "inline"] = "off"

    # 화자 식별(ECAPA) 유사도 임계값 — 실통화 13건 캘리브레이션(대상자 0.605~0.948
    # vs 타화자 0.258~0.417, 중간점 0.511). 외부 데이터 테스트에서 재검증 대상
    speaker_id_threshold: float = Field(default=0.5, gt=0, lt=1)
    # ECAPA 모델 캐시 경로(이미지에 베이크한 위치). None이면 speechbrain 기본 캐시.
    speaker_model_dir: str | None = None

    stt_provider: str = "elevenlabs"
    elevenlabs_stt_model: str = "scribe_v1"
    stt_timeout_seconds: float = Field(default=1800, gt=0)
    audio_download_timeout_seconds: float = Field(default=120, gt=0)
    preview_window_ms: int = Field(default=180_000, gt=0)
    min_speech_ms: int = Field(default=3_000, gt=0)

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
        openai_chat_model=os.getenv("OPENAI_CHAT_MODEL", "gpt-6-luna"),
        openai_analysis_model=os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-6-luna"),
        openai_judge_model=os.getenv("OPENAI_JUDGE_MODEL", "gpt-5.5"),
        openai_stt_model=os.getenv("OPENAI_STT_MODEL", "whisper-1"),
        openai_correction_model=os.getenv("OPENAI_CORRECTION_MODEL", "gpt-6-luna"),
        correction_max_jamo_ratio=os.getenv("CORRECTION_MAX_JAMO_RATIO", "0.45"),
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
        elevenlabs_model=os.getenv("ELEVENLABS_MODEL") or "eleven_v4_turbo",
        elevenlabs_default_voice_id=os.getenv("ELEVENLABS_DEFAULT_VOICE_ID"),
        elevenlabs_speed=os.getenv("ELEVENLABS_SPEED", "1.0"),
        elevenlabs_stability=os.getenv("ELEVENLABS_STABILITY", "0.3"),
        elevenlabs_similarity_boost=os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.75"),
        elevenlabs_style=os.getenv("ELEVENLABS_STYLE", "0.0"),
        minimax_api_key=os.getenv("MINIMAX_API_KEY") or None,
        minimax_model=os.getenv("MINIMAX_MODEL") or "speech-2.8-turbo",
        voice_emotion=os.getenv("VOICE_EMOTION") or "off",
        minimax_emotion=os.getenv("MINIMAX_EMOTION") or None,
        minimax_intensity=os.getenv("MINIMAX_INTENSITY") or "0",
        speaker_id_threshold=os.getenv("SPEAKER_ID_THRESHOLD", "0.5"),
        speaker_model_dir=os.getenv("SPEAKER_MODEL_DIR"),
        stt_provider=os.getenv("STT_PROVIDER", "elevenlabs"),
        elevenlabs_stt_model=os.getenv("ELEVENLABS_STT_MODEL", "scribe_v1"),
        stt_timeout_seconds=os.getenv("STT_TIMEOUT_SECONDS", "1800"),
        audio_download_timeout_seconds=os.getenv(
            "AUDIO_DOWNLOAD_TIMEOUT_SECONDS", "120"
        ),
        preview_window_ms=os.getenv("PREVIEW_WINDOW_MS", "180000"),
        min_speech_ms=os.getenv("MIN_SPEECH_MS", "3000"),
        max_audio_bytes=os.getenv("MAX_AUDIO_BYTES"),
        temp_dir=os.getenv("TEMP_DIR"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
