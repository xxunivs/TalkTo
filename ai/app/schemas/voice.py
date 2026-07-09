from pydantic import Field, HttpUrl, model_validator

from app.schemas.common import ApiModel


class VoiceTranscriptionResponse(ApiModel):
    text: str
    provider: str
    model: str


class VoiceSampleSegment(ApiModel):
    """대상자 화자 구간 하나. 통화 오디오 URL + (선택) 구간 [startMs, endMs)."""

    # 녹음 오디오 presigned URL. startMs/endMs를 주면 AI가 그 구간만 잘라 쓴다.
    audio_url: HttpUrl
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_range(self) -> "VoiceSampleSegment":
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("startMs와 endMs는 함께 주거나 함께 비워야 합니다.")
        if self.start_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("endMs는 startMs보다 커야 합니다.")
        return self


class VoiceCloneRequest(ApiModel):
    # 클론 음성 이름(대상자 식별용). 고인 목소리 잘 들리는 구간 샘플로 등록.
    name: str = Field(min_length=1, max_length=200)
    # (단일) BE가 TargetVoiceSample에서 잘라 만든 샘플 클립 하나의 presigned URL.
    sample_audio_url: HttpUrl | None = None
    # (다구간) 대상자 화자 구간들 — AI가 각 구간을 잘라 이어붙여 한 목소리로 학습.
    # 화자 식별(subjectSpeakerLabel)로 전 통화에서 모은 대상자 구간을 넘기면 된다.
    samples: list[VoiceSampleSegment] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "VoiceCloneRequest":
        has_single = self.sample_audio_url is not None
        has_multi = bool(self.samples)
        if has_single == has_multi:
            raise ValueError("sampleAudioUrl 또는 samples 중 정확히 하나만 주세요.")
        return self


class VoiceCloneResponse(ApiModel):
    voice_id: str
    provider: str


class SpeechSynthesisRequest(ApiModel):
    text: str = Field(min_length=1, max_length=20_000)
    # 대상자 클론 음성 id. 미지정 시 ELEVENLABS_DEFAULT_VOICE_ID로 폴백.
    voice_id: str | None = Field(default=None, max_length=200)
    # 말하는 속도(0.7~1.2, 1.0=기본). 미지정 시 서버 ELEVENLABS_SPEED로 폴백.
    speed: float | None = Field(default=None, ge=0.7, le=1.2)
