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
    # 대화·분석·교정은 gpt-6-luna 하나로 쓴다. 싸고(1M 토큰당 입력 $0.10 / 출력 $0.50)
    # 대화 1턴 1.1초로 gpt-4.1-mini(3.7초)보다 빠르다.
    openai_chat_model: str = "gpt-6-luna"
    # 대답 생성의 추론 정도(reasoning_effort). 기본값(빈 값)이면 모델이 답 전에 ~200토큰 생각해
    # 1턴이 3초가 넘는다. none 이면 1.5초 안팎(low 도 가끔 100토큰 넘게 생각한다, 2026-10-13 측정).
    openai_chat_reasoning_effort: Literal["none", "low", "medium", "high"] | None = "none"
    openai_analysis_model: str = "gpt-6-luna"
    # 평가 채점(LLM judge) 모델 — 비용 때문에 생성과 같은 gpt-6-luna 를 쓴다
    openai_judge_model: str = "gpt-6-luna"
    openai_stt_model: str = "whisper-1"
    openai_correction_model: str = "gpt-6-luna"
    # 교정 발음 유사도 게이트(자모 편집거리 비율 상한). 실통화 13건으로 맞춘 초기값
    correction_max_jamo_ratio: float = Field(default=0.45, gt=0, le=1)
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = Field(default=1536, gt=0)
    openai_timeout_seconds: float = Field(default=60, gt=0)
    openai_max_retries: int = Field(default=2, ge=0)
    # 대화 대답 생성. BE 는 45초에 끊는다. 시간 한도 × (다시 하기 + 1) 이 45초보다 짧아야 BE 가 포기한 뒤에도
    # 혼자 돌며 돈을 쓰지 않는다(기본 60초 × 3번이면 3분까지 돌았다).
    openai_chat_timeout_seconds: float = Field(default=20, gt=0)
    openai_chat_max_retries: int = Field(default=1, ge=0)
    # 녹음 분석(인물·민감·기억·말투·요약)과 통찰. 긴 녹음은 답 하나에 60초가 넘게 걸려 늘 실패했다.
    # BE 는 이 호출을 30분까지 기다린다.
    openai_analysis_timeout_seconds: float = Field(default=300, gt=0)
    openai_analysis_max_retries: int = Field(default=1, ge=0)

    # 목소리: 새 목소리는 늘 Fish Audio(drama-3)에서 만든다. 합성·지우기는 voice_id 로 만든 회사를
    # 알아본다(fish_… → Fish, TalkTo… → MiniMax, 그 밖 → ElevenLabs). 예전 회사 목소리는 다시 만들기 전까지 쓴다.
    fish_audio_api_key: SecretStr | None = None
    # voice_id 없이 회사를 고를 때의 기본값. 새 목소리 만들기는 이 값과 무관하게 fish 다.
    tts_provider: str = "fish"
    # ElevenLabs: 녹음 전사(Scribe)와 예전 목소리 합성·지우기
    elevenlabs_api_key: SecretStr | None = None
    # 기본은 v4 Turbo: 목소리가 조금 달라도 감정 표현이 좋고 AI 느낌이 덜하다(청취 비교로 정함).
    # 즉시 복제 목소리(IVC)가 아니면 provider 가 v2 로 되돌린다.
    elevenlabs_model: str | None = "eleven_v4_turbo"
    elevenlabs_default_voice_id: str | None = None
    # 말하는 속도(ElevenLabs speed). 1.0=기본, <1 느리게. 예전 ElevenLabs·MiniMax 목소리에만 쓴다(Fish 는 안 씀).
    # 0.7~1.2 권장. 요청에서 speed로 오버라이드 가능.
    elevenlabs_speed: float = Field(default=1.0, ge=0.7, le=1.2)
    # 목소리 결. 대상자마다 달라야 해서 요청에서 덮어쓸 수 있다. 여기 값은 기본값이다.
    # 흔들림을 조금 허용해 표현을 살리고(0.3), 원본 음색은 유지하고(0.75),
    # 과장 스타일은 끈다(0.0).
    elevenlabs_stability: float = Field(default=0.3, ge=0.0, le=1.0)
    elevenlabs_similarity_boost: float = Field(default=0.75, ge=0.0, le=1.0)
    elevenlabs_style: float = Field(default=0.0, ge=0.0, le=1.0)
    # MiniMax: 예전에 만든 목소리(TalkTo…)의 합성·지우기만. 새 목소리는 만들지 않는다.
    minimax_api_key: SecretStr | None = None
    # turbo: 대화는 첫 소리가 빨라야 해서. hd 는 더 곱지만 느리다.
    minimax_model: str = "speech-2.8-turbo"
    # 비우면 문장에 맞춰 MiniMax 가 고른다(happy, sad, calm ...).
    minimax_emotion: str | None = None
    # 목소리 결: +면 부드럽게, -면 힘 있게(-100~100). 0 이면 보내지 않는다.
    minimax_intensity: int = Field(default=0, ge=-100, le=100)
    # 대답 음성 감정(MiniMax 예전 목소리만): off = 목소리 회사가 글을 보고 정함, inline = 대답 LLM 이
    # 같은 호출에서 감정을 하나 골라 붙인다(pipeline/s4_voice/b_emotion.py).
    voice_emotion: Literal["off", "inline"] = "off"
    # Fish(drama-3) 목소리 대답의 감정. 대답 LLM 이 같은 호출에서 함께 정한다(LLM 을 더 부르지 않는다).
    # off = 지금처럼(표시 없이 글만), cue = 감정 이름 하나 → Fish 가 맨 앞에 한국어 연기 표시(FISH_CUES)를 붙인다,
    # inline = 대답 안 정해진 연기 표시 최대 2개(speechText). 대표님 청취 판정 전에는 운영에서 켜지 않는다.
    fish_emotion: Literal["off", "cue", "inline"] = "off"

    # 화자 식별(ECAPA) 유사도 임계값 — 실통화 13건으로 맞춘 값(대상자 0.605~0.948
    # vs 타화자 0.258~0.417, 중간점 0.511)
    speaker_id_threshold: float = Field(default=0.5, gt=0, lt=1)
    # ECAPA 모델 폴더. 도커 이미지는 /opt/ecapa 에 미리 받아 둔다(Dockerfile). None이면 speechbrain 기본 캐시.
    speaker_model_dir: str | None = None

    stt_provider: str = "elevenlabs"
    # scribe_v1 은 지원 종료(ElevenLabs 공지, 2026-07-09). v2 를 이름으로 고정한다.
    elevenlabs_stt_model: str = "scribe_v2"
    stt_timeout_seconds: float = Field(default=1800, gt=0)
    audio_download_timeout_seconds: float = Field(default=120, gt=0)
    preview_window_ms: int = Field(default=180_000, gt=0)
    min_speech_ms: int = Field(default=3_000, gt=0)

    max_audio_bytes: int | None = Field(default=None, gt=0)
    # 임시 파일을 둘 곳. 실제로는 그 아래 talkto-ai 폴더 하나에 모은다(infra/scratch.py). None 이면 시스템 임시 폴더.
    temp_dir: Path | None = None

    # 중간계층(orchestrator/) — /v1/persona/responses 를 중간계층으로 처리. 꺼져 있으면 기존 경로.
    persona_orchestrator: bool = False
    # 턴별 추적 jsonl 위치(대화 원문 포함 → git 제외 경로). None 이면 안 남김.
    orchestrator_trace_dir: Path | None = None
    # 조립기: legacy(기존과 동일) | structured(공통 규칙 md + 페르소나 + 말투 재료)
    orchestrator_assembler: Literal["legacy", "structured"] = "legacy"
    # structured 조립의 공통 규칙: legacy = 예전 규칙(+사인 정책 절) / layered = 4층 L1 모듈(사인 정책은 모듈 변형으로)
    # 기본 layered: 지시문이 4층이면 같은 줄을 지워 규칙이 두 벌 겹치지 않는다. structured 조립일 때만 쓰인다.
    orchestrator_rules: Literal["legacy", "layered"] = "layered"
    # structured 조립에서 실제 주고받기를 넣는 곳: system(지시문 안 글) / messages(기억 뒤 대화 메시지)
    # 기본 messages: 실험에서 정답률 58%→68%. 등록부에 주고받기가 있을 때만 효과가 있다.
    orchestrator_shots: Literal["system", "messages"] = "messages"
    # 페르소나 지시문 조립(/v1/persona/assembly, 로컬 s3_c): legacy = assemble_persona_prompt / layered = 4층(pipeline/persona_prompt)
    # 기본 layered: 실험에서 지어내기 33%→24%. 예전 조립은 PERSONA_PROMPT_BUILDER=legacy 로 되돌린다.
    persona_prompt_builder: Literal["legacy", "layered"] = "layered"
    # 4층 조립에서 말투 카드를 붙이는 speechExamples 최소 개수. 낮추는 값은 세션 17 오프라인 비교 뒤 재헌이 정한다.
    style_card_min_utts: int = Field(default=30, ge=1, le=50)
    # 사인·임종 질문 기본 설정(요청에 deathTalk 가 없을 때): intake = 설문 그대로 / off = 끔 / on = 켬
    persona_death_talk: Literal["intake", "off", "on"] = "intake"
    # 페르소나 등록부(profile.json·말투 카드·실제 발화). None 이면 지시문에서 추정만.
    orchestrator_persona_dir: Path | None = None
    # 사전 판정: noop | coverage(질문 화제어 커버율 < 임계면 "모른다·되묻기" 지시, kiwipiepy 필요)
    orchestrator_precheck: Literal["noop", "coverage", "coverage_v11"] = "noop"
    orchestrator_precheck_threshold: float = Field(default=0.6, gt=0, le=1)
    # 사후 가드. v1 이면 structured 조립기가 근거 헤더를 요구하고 가드가 검사한다.
    orchestrator_guard: Literal["pass", "v1", "v11"] = "pass"
    # 언어 누출 가드(orchestrator/lang_guard.py): off / flag(기록만) / fix(걸린 문장 삭제)
    orchestrator_lang_guard: Literal["off", "flag", "fix"] = "off"
    # 기억 선택: passthrough(BE 가 보낸 그대로) | tiered(장기 전부 + 단기만 분해 단서 top-k, 단기 임베딩은 AI 서버가 함)
    orchestrator_selector: Literal["passthrough", "tiered"] = "passthrough"
    orchestrator_selector_st_top_k: int = Field(default=5, ge=0, le=50)
    orchestrator_selector_threshold: float = Field(default=0.18, ge=-1, le=1)
    # BE 가 기억을 전부 보낼 때의 상한. 넘으면 경고 로그(+중간계층 trace)만 남기고 자르지 않는다.
    # 넘으면 검색으로 바꾸는 것은 BE 몫. 400 은 제안값(실험 페르소나 기억 309개 ≈ 1.9만 토큰), 최종 값은 재헌 결정.
    memory_full_max: int = Field(default=400, gt=0)
    # 기억 검색 질문 다시 쓰기(/v1/embeddings/query 에 history 가 올 때만). on 이면 질문 하나당 LLM 1회가 검색 앞에 붙는다.
    query_rewrite: Literal["off", "on"] = "off"
    # 말투 덧붙임: 지시문에 말투 카드와 "짧게" 말투 규칙이 있으면 마지막 가족 말 바로 앞에 system 한 줄 묶음을 넣는다.
    # 기본 on: 세션 18 실험(덧말 막는 줄과 함께, E1)에서 어미 거리 −0.08, 사실 정답 56.5%→71.7%.
    # 가져온 지침(persona.fixedPrompt)에는 넣지 않는다(4층 생성기 글이 아니라 실험 범위 밖). 덧붙임 예시가 반말이라
    # 존댓말로 대하는 상대(가족 표 듣는 사람 줄이 존댓말)·공개 본인 페르소나에도 넣지 않는다(late_style_note).
    style_late_note: Literal["off", "on"] = "on"
    # 기억이 적을 때 지시(LOW_MEMORY_NOTE). 기억 수가 LOW_MEMORY_MAX 이하일 때만 턴마다 붙는다(4층 레포 세션 19).
    # 가져온 지침(persona.fixedPrompt)에는 넣지 않는다(MVP 때와 같은 메시지).
    low_memory_note: Literal["off", "on"] = "on"
    low_memory_max: int = Field(default=5, ge=0)
    # 사전 판정은 기억이 이 수 이상일 때만(적으면 모든 질문이 '모른다'가 된다). 기억 0개면 늘 건너뜀.
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
        """배포 환경(staging·production). 토큰이 없으면 막고 API 문서를 닫는다."""
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
        openai_chat_reasoning_effort=os.getenv("OPENAI_CHAT_REASONING_EFFORT", "none").strip() or None,
        openai_analysis_model=os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-6-luna"),
        openai_judge_model=os.getenv("OPENAI_JUDGE_MODEL", "gpt-6-luna"),
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
        openai_chat_timeout_seconds=os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "20"),
        openai_chat_max_retries=os.getenv("OPENAI_CHAT_MAX_RETRIES", "1"),
        openai_analysis_timeout_seconds=os.getenv("OPENAI_ANALYSIS_TIMEOUT_SECONDS", "300"),
        openai_analysis_max_retries=os.getenv("OPENAI_ANALYSIS_MAX_RETRIES", "1"),
        # 배포(BE deploy.py)는 os.getenv 에 글자 그대로 적힌 이름만 운영 비밀값으로 옮긴다. 변수로 만들지 않는다.
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
        minimax_emotion=os.getenv("MINIMAX_EMOTION") or None,
        minimax_intensity=os.getenv("MINIMAX_INTENSITY") or "0",
        speaker_id_threshold=os.getenv("SPEAKER_ID_THRESHOLD", "0.5"),
        speaker_model_dir=os.getenv("SPEAKER_MODEL_DIR"),
        stt_provider=os.getenv("STT_PROVIDER", "elevenlabs"),
        elevenlabs_stt_model=os.getenv("ELEVENLABS_STT_MODEL") or "scribe_v2",
        stt_timeout_seconds=os.getenv("STT_TIMEOUT_SECONDS", "1800"),
        audio_download_timeout_seconds=os.getenv(
            "AUDIO_DOWNLOAD_TIMEOUT_SECONDS", "120"
        ),
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
        orchestrator_selector=os.getenv("ORCHESTRATOR_SELECTOR", "passthrough").strip() or "passthrough",
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
