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
    # 대화, 분석, 교정 공용 모델 (1M 토큰당 입력 $0.10, 출력 $0.50, gpt-4.1-mini 대비 1턴 3.7초 -> 1.1초)
    openai_chat_model: str = "gpt-6-luna"
    # 대답 reasoning_effort (빈 값이면 약 200토큰 추론으로 1턴 3초 초과, none 이면 1.5초 안팎)
    # low 도 가끔 100토큰 넘게 추론 (2026-10-13 측정)
    openai_chat_reasoning_effort: Literal["none", "low", "medium", "high"] | None = "none"
    openai_analysis_model: str = "gpt-6-luna"
    # 평가 채점(LLM judge) 모델 (비용상 생성과 동일)
    openai_judge_model: str = "gpt-6-luna"
    openai_stt_model: str = "whisper-1"
    openai_correction_model: str = "gpt-6-luna"
    # 교정 발음 유사도 게이트 (자모 편집거리 비율 상한, 실통화 13건 기준 초기값)
    correction_max_jamo_ratio: float = Field(default=0.45, gt=0, le=1)
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = Field(default=1536, gt=0)
    openai_timeout_seconds: float = Field(default=60, gt=0)
    openai_max_retries: int = Field(default=2, ge=0)
    # BE 45초 한도 안에서 끝나도록 (시간 한도 x (재시도 + 1) < 45초, 이전 기본값은 3분까지 실행)
    openai_chat_timeout_seconds: float = Field(default=20, gt=0)
    openai_chat_max_retries: int = Field(default=1, ge=0)
    # 녹음 분석, 통찰 (긴 녹음은 답 하나에 60초 초과, BE 대기 한도 30분)
    openai_analysis_timeout_seconds: float = Field(default=300, gt=0)
    openai_analysis_max_retries: int = Field(default=1, ge=0)

    # 새 목소리는 항상 Fish Audio(drama-3), 합성과 삭제는 voice_id 접두어로 제공사 판별
    # (fish_ -> Fish, TalkTo -> MiniMax, 그 외 -> ElevenLabs, 예전 목소리는 재생성 전까지 사용)
    fish_audio_api_key: SecretStr | None = None
    # voice_id 없을 때 기본 제공사 (새 목소리 생성은 무관하게 fish)
    tts_provider: str = "fish"
    # ElevenLabs: 녹음 전사(Scribe), 예전 목소리 합성과 삭제
    elevenlabs_api_key: SecretStr | None = None
    # v4 Turbo: 음색 차이보다 감정 표현, 자연스러움 우선 (청취 비교, IVC 가 아니면 provider 가 v2 로)
    elevenlabs_model: str | None = "eleven_v4_turbo"
    elevenlabs_default_voice_id: str | None = None
    # 말하는 속도 (1.0 기본, 0.7~1.2, 요청 speed 로 덮어쓰기, 예전 ElevenLabs, MiniMax 목소리 전용)
    elevenlabs_speed: float = Field(default=1.0, ge=0.7, le=1.2)
    # 목소리 결 기본값 (요청에서 대상자별 덮어쓰기)
    # 표현용 흔들림 허용 0.3, 원본 음색 유지 0.75, 과장 스타일 끔 0.0
    elevenlabs_stability: float = Field(default=0.3, ge=0.0, le=1.0)
    elevenlabs_similarity_boost: float = Field(default=0.75, ge=0.0, le=1.0)
    elevenlabs_style: float = Field(default=0.0, ge=0.0, le=1.0)
    # MiniMax: 예전 목소리(TalkTo 접두어) 합성과 삭제 전용, 새 목소리 생성 없음
    minimax_api_key: SecretStr | None = None
    # turbo: 대화 첫 소리 속도 우선 (hd 는 음질 우위, 느림)
    minimax_model: str = "speech-2.8-turbo"
    # 빈 값이면 MiniMax 가 문장 기반 선택 (happy, sad, calm 등)
    minimax_emotion: str | None = None
    # 목소리 결 (+ 부드럽게, - 힘 있게, -100~100, 0 이면 미전송)
    minimax_intensity: int = Field(default=0, ge=-100, le=100)
    # 대답 음성 감정 (MiniMax 예전 목소리 전용): off = 제공사가 글 기반 결정
    # inline = 대답 LLM 이 같은 호출에서 감정 하나 선택 (pipeline/s4_voice/b_emotion.py)
    voice_emotion: Literal["off", "inline"] = "off"
    # Fish 대답 감정 (대답 LLM 같은 호출, 추가 호출 없음, 청취 판정 전 운영 미사용)
    # off = 글만, cue = 감정 이름 -> 맨 앞 연기 표시(FISH_CUES), inline = 대답 안 연기 표시 최대 2개
    fish_emotion: Literal["off", "cue", "inline"] = "off"
    # 기쁜 대답의 Fish temperature (기본 0.7, 기쁨 시 음이 높아져 블라인드 청취 6안 중 0.5 선택)
    # 0 이하면 미전송
    fish_happy_temperature: float = Field(default=0.5, le=1)

    # 화자 식별(ECAPA) 유사도 임계값 (실통화 13건: 대상자 0.605~0.948, 타화자 0.258~0.417)
    speaker_id_threshold: float = Field(default=0.5, gt=0, lt=1)
    # ECAPA 모델 폴더 (도커 이미지는 /opt/ecapa, None 이면 speechbrain 기본 캐시)
    speaker_model_dir: str | None = None

    stt_provider: str = "elevenlabs"
    # scribe_v1 지원 종료로 v2 고정 (ElevenLabs 공지, 2026-07-09)
    elevenlabs_stt_model: str = "scribe_v2"
    stt_timeout_seconds: float = Field(default=1800, gt=0)
    audio_download_timeout_seconds: float = Field(default=120, gt=0)
    preview_window_ms: int = Field(default=180_000, gt=0)
    min_speech_ms: int = Field(default=3_000, gt=0)

    max_audio_bytes: int | None = Field(default=None, gt=0)
    # 임시 파일 위치 (그 아래 talkto-ai 폴더, app/temp_files.py, None 이면 시스템 임시 폴더)
    temp_dir: Path | None = None

    # /v1/persona/responses 중간계층(orchestrator/) 처리 여부 (꺼지면 기존 경로)
    persona_orchestrator: bool = False
    # 턴별 추적 jsonl 위치 (대화 원문 포함이라 git 제외 경로, None 이면 미기록)
    orchestrator_trace_dir: Path | None = None
    # 조립기: legacy (기존), structured (공통 규칙 md + 페르소나 + 말투 재료)
    orchestrator_assembler: Literal["legacy", "structured"] = "legacy"
    # structured 조립 공통 규칙: legacy = 예전 규칙 (+사인 정책 절), layered = 4층 L1 모듈
    # 기본 layered: 4층 지시문과 규칙 중복 방지 (structured 조립 전용)
    orchestrator_rules: Literal["legacy", "layered"] = "layered"
    # structured 조립의 실제 주고받기 위치: system (지시문 안), messages (기억 뒤 대화 메시지)
    # 기본 messages: 실험 정답률 58% -> 68% (등록부에 주고받기가 있을 때만 효과)
    orchestrator_shots: Literal["system", "messages"] = "messages"
    # 페르소나 지시문 조립: legacy = assemble_persona_prompt, layered = 4층 (pipeline/persona_prompt)
    # 기본 layered: 실험 지어내기 33% -> 24%
    persona_prompt_builder: Literal["legacy", "layered"] = "layered"
    # 4층 조립의 말투 카드 부착 최소 speechExamples 개수 (하향은 오프라인 비교 후 결정)
    style_card_min_utts: int = Field(default=30, ge=1, le=50)
    # 사인, 임종 질문 기본값 (요청에 deathTalk 없을 때): intake = 설문 그대로, off, on
    persona_death_talk: Literal["intake", "off", "on"] = "intake"
    # 페르소나 등록부 (profile.json, 말투 카드, 실제 발화, None 이면 지시문 기반 추정만)
    orchestrator_persona_dir: Path | None = None
    # 사전 판정: noop, coverage (화제어 커버율 < 임계면 모른다, 되묻기 지시, kiwipiepy 필요)
    orchestrator_precheck: Literal["noop", "coverage", "coverage_v11"] = "noop"
    orchestrator_precheck_threshold: float = Field(default=0.6, gt=0, le=1)
    # 사후 가드 (v1 이면 structured 조립기가 근거 헤더 요구, 가드가 검사)
    orchestrator_guard: Literal["pass", "v1", "v11"] = "pass"
    # 언어 누출 가드 (orchestrator/lang_guard.py): off, flag (기록만), fix (걸린 문장 삭제)
    orchestrator_lang_guard: Literal["off", "flag", "fix"] = "off"
    # 기억 선택: passthrough (BE 전달 그대로), tiered (장기 전부 + 단기 top-k, 단기 임베딩은 AI 서버)
    orchestrator_selector: Literal["passthrough", "tiered"] = "passthrough"
    orchestrator_selector_st_top_k: int = Field(default=5, ge=0, le=50)
    orchestrator_selector_threshold: float = Field(default=0.18, ge=-1, le=1)
    # BE 전체 기억 전송 상한 (초과 시 경고 로그만, 자르지 않음, 검색 전환은 BE 몫)
    # 400 은 제안값 (실험 페르소나 기억 309개 약 1.9만 토큰)
    memory_full_max: int = Field(default=400, gt=0)
    # 기억 검색 질문 재작성 (history 가 올 때만, on 이면 질문당 LLM 1회 추가)
    query_rewrite: Literal["off", "on"] = "off"
    # 말투 덧붙임: 말투 카드, 짧게 규칙이 있으면 마지막 가족 말 앞에 system 묶음 (실험 사실 정답 56.5% -> 71.7%)
    # 제외: fixedPrompt (실험 범위 밖), 존댓말 상대, 공개 본인 페르소나 (예시가 반말, late_style_note)
    style_late_note: Literal["off", "on"] = "on"
    # 기억 부족 지시 (기억 수 LOW_MEMORY_MAX 이하일 때 턴마다, fixedPrompt 제외)
    low_memory_note: Literal["off", "on"] = "on"
    low_memory_max: int = Field(default=5, ge=0)
    # 사전 판정 최소 기억 수 (적으면 모든 질문이 모른다로 판정)
    orchestrator_precheck_min_memories: int = Field(default=30, ge=0)

    @field_validator(
        "ai_server_token",
        "openai_api_key",
        "fish_audio_api_key",
        "elevenlabs_api_key",
        "elevenlabs_model",
        "elevenlabs_default_voice_id",
        "max_audio_bytes",
        "temp_dir",
        "orchestrator_trace_dir",
        "orchestrator_persona_dir",
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

    @property
    def deployed(self) -> bool:
        """배포 환경 여부 (staging, production: 토큰 없으면 차단, API 문서 닫음)"""
        return self.app_env in ("staging", "production")


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)

    return Settings(
        app_env=os.getenv("APP_ENV", "local"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        ai_server_token=os.getenv("AI_SERVER_TOKEN"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_chat_model=os.getenv("OPENAI_CHAT_MODEL", "gpt-6-luna"),
        openai_chat_reasoning_effort=os.getenv("OPENAI_CHAT_REASONING_EFFORT", "none").strip()
        or None,
        openai_analysis_model=os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-6-luna"),
        openai_judge_model=os.getenv("OPENAI_JUDGE_MODEL", "gpt-6-luna"),
        openai_stt_model=os.getenv("OPENAI_STT_MODEL", "whisper-1"),
        openai_correction_model=os.getenv("OPENAI_CORRECTION_MODEL", "gpt-6-luna"),
        correction_max_jamo_ratio=os.getenv("CORRECTION_MAX_JAMO_RATIO", "0.45"),
        openai_embedding_model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        openai_embedding_dimensions=os.getenv("OPENAI_EMBEDDING_DIMENSIONS", "1536"),
        openai_timeout_seconds=os.getenv("OPENAI_TIMEOUT_SECONDS", "60"),
        openai_max_retries=os.getenv("OPENAI_MAX_RETRIES", "2"),
        openai_chat_timeout_seconds=os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "20"),
        openai_chat_max_retries=os.getenv("OPENAI_CHAT_MAX_RETRIES", "1"),
        openai_analysis_timeout_seconds=os.getenv("OPENAI_ANALYSIS_TIMEOUT_SECONDS", "300"),
        openai_analysis_max_retries=os.getenv("OPENAI_ANALYSIS_MAX_RETRIES", "1"),
        # BE deploy.py 는 os.getenv 의 문자열 리터럴 이름만 운영 비밀값으로 복사 (변수화 금지)
        fish_audio_api_key=os.getenv("FISH_AUDIO_API_KEY") or None,
        tts_provider=os.getenv("TTS_PROVIDER") or "fish",
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
        fish_emotion=os.getenv("FISH_EMOTION", "off").strip().lower() or "off",
        fish_happy_temperature=os.getenv("FISH_HAPPY_TEMPERATURE", "0.5"),
        minimax_emotion=os.getenv("MINIMAX_EMOTION") or None,
        minimax_intensity=os.getenv("MINIMAX_INTENSITY") or "0",
        speaker_id_threshold=os.getenv("SPEAKER_ID_THRESHOLD", "0.5"),
        speaker_model_dir=os.getenv("SPEAKER_MODEL_DIR"),
        stt_provider=os.getenv("STT_PROVIDER", "elevenlabs"),
        elevenlabs_stt_model=os.getenv("ELEVENLABS_STT_MODEL") or "scribe_v2",
        stt_timeout_seconds=os.getenv("STT_TIMEOUT_SECONDS", "1800"),
        audio_download_timeout_seconds=os.getenv("AUDIO_DOWNLOAD_TIMEOUT_SECONDS", "120"),
        preview_window_ms=os.getenv("PREVIEW_WINDOW_MS", "180000"),
        min_speech_ms=os.getenv("MIN_SPEECH_MS", "3000"),
        max_audio_bytes=os.getenv("MAX_AUDIO_BYTES"),
        temp_dir=os.getenv("TEMP_DIR"),
        persona_orchestrator=os.getenv("PERSONA_ORCHESTRATOR", "false").strip() or "false",
        orchestrator_trace_dir=os.getenv("ORCHESTRATOR_TRACE_DIR"),
        orchestrator_assembler=os.getenv("ORCHESTRATOR_ASSEMBLER", "legacy").strip() or "legacy",
        orchestrator_rules=os.getenv("ORCHESTRATOR_RULES", "layered").strip() or "layered",
        orchestrator_shots=os.getenv("ORCHESTRATOR_SHOTS", "messages").strip() or "messages",
        persona_prompt_builder=os.getenv("PERSONA_PROMPT_BUILDER", "layered").strip() or "layered",
        persona_death_talk=os.getenv("PERSONA_DEATH_TALK", "intake").strip() or "intake",
        style_card_min_utts=os.getenv("STYLE_CARD_MIN_UTTS", "30"),
        orchestrator_persona_dir=os.getenv("ORCHESTRATOR_PERSONA_DIR"),
        orchestrator_precheck=os.getenv("ORCHESTRATOR_PRECHECK", "noop").strip() or "noop",
        orchestrator_precheck_threshold=os.getenv("ORCHESTRATOR_PRECHECK_THRESHOLD", "0.6"),
        orchestrator_guard=os.getenv("ORCHESTRATOR_GUARD", "pass").strip() or "pass",
        orchestrator_lang_guard=os.getenv("ORCHESTRATOR_LANG_GUARD", "off").strip() or "off",
        orchestrator_selector=os.getenv("ORCHESTRATOR_SELECTOR", "passthrough").strip()
        or "passthrough",
        orchestrator_selector_st_top_k=os.getenv("ORCHESTRATOR_SELECTOR_ST_TOPK", "5"),
        orchestrator_selector_threshold=os.getenv("ORCHESTRATOR_SELECTOR_THRESHOLD", "0.18"),
        memory_full_max=os.getenv("MEMORY_FULL_MAX", "400"),
        query_rewrite=os.getenv("QUERY_REWRITE", "off").strip() or "off",
        style_late_note=os.getenv("STYLE_LATE_NOTE", "on").strip() or "on",
        low_memory_note=os.getenv("LOW_MEMORY_NOTE", "on").strip() or "on",
        low_memory_max=os.getenv("LOW_MEMORY_MAX", "5"),
        orchestrator_precheck_min_memories=os.getenv("ORCHESTRATOR_PRECHECK_MIN_MEMORIES", "30"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
