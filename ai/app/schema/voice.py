from typing import Literal

from pydantic import Field, HttpUrl, model_validator

from app.schema.field_rule import ApiModel
from app.schema.memory import PersistedTranscriptSegment


class VoiceTranscriptionResponse(ApiModel):
    text: str
    provider: str
    model: str


class AnswerTranscriptionResponse(ApiModel):
    """말로 답하기 받아쓴 글 (화자 분리 없음, 녹음과 ElevenLabs 기록 미보관)"""

    text: str
    provider: str
    model: str


class VoiceSampleSegment(ApiModel):
    """대상자 화자 구간 [startMs, endMs) (구간 필수: 통째 녹음은 상대 목소리 혼입)"""

    audio_url: HttpUrl
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_range(self) -> "VoiceSampleSegment":
        if self.end_ms <= self.start_ms:
            raise ValueError("endMs는 startMs보다 커야 합니다.")
        return self


class VoiceCloneRequest(ApiModel):
    # 클론 음성 이름 (대상자 식별용)
    name: str = Field(min_length=1, max_length=200)
    # 단일 모드: BE 가 잘라 둔 샘플 클립 presigned URL
    sample_audio_url: HttpUrl | None = None
    # 다구간 모드: 대상자 구간들 (AI 가 잘라 이어 붙여 한 목소리로 학습)
    samples: list[VoiceSampleSegment] | None = Field(default=None, max_length=100)
    # 예전 BE 호환 칸 (값과 무관하게 새 목소리는 Fish Audio)
    provider: Literal["fish", "elevenlabs", "minimax"] | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "VoiceCloneRequest":
        has_single = self.sample_audio_url is not None
        has_multi = bool(self.samples)
        if has_single == has_multi:
            raise ValueError("sampleAudioUrl 또는 samples 중 정확히 하나만 주세요.")
        return self


class SubjectSampleCollectionRequest(ApiModel):
    """한 통화의 대상자 구간 추출 요청 (클론 없음, BE 가 대상자별 풀에 누적)"""

    # 원본 녹음 presigned URL (반환 구간에 포함)
    audio_url: HttpUrl
    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)
    # 확정 대상자 라벨 (None 이면 빈 목록)
    subject_speaker_label: str | None = Field(default=None, max_length=120)


class SubjectSampleCollectionResponse(ApiModel):
    # 대상자 발화 구간 전부 (상한, 선별은 클론 시점)
    samples: list[VoiceSampleSegment] = Field(default_factory=list)


class VoiceCloneFromTranscriptRequest(ApiModel):
    """전사 결과 기반 대상자 구간 자동 수집 클론 요청 (녹음 1건 단위)"""

    name: str = Field(min_length=1, max_length=200)
    audio_url: HttpUrl
    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)
    # 확정 대상자 라벨 (None 이면 클론 안 함)
    subject_speaker_label: str | None = Field(default=None, max_length=120)


class VoiceCloneResponse(ApiModel):
    voice_id: str
    provider: str


class SpeechSynthesisRequest(ApiModel):
    text: str = Field(min_length=1, max_length=20_000)
    # 대상자 클론 음성 id (접두어로 제공사 판별, 없으면 ELEVENLABS_DEFAULT_VOICE_ID)
    voice_id: str | None = Field(default=None, max_length=200)
    # 아래 속도, 결은 예전 ElevenLabs, MiniMax 목소리 전용 (Fish 미사용)
    # 말하는 속도 (0.7~1.2, 없으면 ELEVENLABS_SPEED)
    speed: float | None = Field(default=None, ge=0.7, le=1.2)
    # 대상자별 목소리 결 (없으면 서버 기본값)
    stability: float | None = Field(default=None, ge=0.0, le=1.0)
    similarity_boost: float | None = Field(default=None, ge=0.0, le=1.0)
    style: float | None = Field(default=None, ge=0.0, le=1.0)
    # 대답 감정 (None 이면 제공사 판단, MiniMax 는 자체 감정으로 변환, Fish 는 cue 일 때 연기 표시, ElevenLabs 미사용)
    emotion: Literal["neutral", "calm", "warm", "happy", "sad", "surprised", "worried"] | None = (
        None
    )


class VoiceDeleteRequest(ApiModel):
    voice_id: str = Field(min_length=1, max_length=255)
    # 목소리 제공사 (voice_id 접두어 우선, 접두어로 모를 때만 사용, 없으면 ElevenLabs)
    provider: Literal["fish", "elevenlabs", "minimax"] | None = None


class OkResponse(ApiModel):
    ok: bool = True
