from pydantic import Field, HttpUrl, model_validator

from app.data_schema.common import ApiModel
from app.data_schema.memory import PersistedTranscriptSegment


class VoiceTranscriptionResponse(ApiModel):
    text: str
    provider: str
    model: str


class VoiceSampleSegment(ApiModel):
    """대상자 화자 구간 하나. 녹음 오디오 URL + 구간 [startMs, endMs).

    구간은 필수다. samples[]는 "대상자가 말한 구간들"을 담는 용도라, 구간 없이 녹음을
    통째로 넣으면 상대 화자 목소리가 섞여 엉뚱한 목소리로 학습된다. 이미 잘라놓은 클립
    하나를 쓰려면 VoiceCloneRequest.sample_audio_url(단일 모드)로 보낸다.
    """

    audio_url: HttpUrl
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_range(self) -> "VoiceSampleSegment":
        if self.end_ms <= self.start_ms:
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


class SubjectSampleCollectionRequest(ApiModel):
    """한 통화에서 대상자 구간만 뽑아 반환(클론하지 않음).

    누적 풀(B안): BE가 통화가 들어올 때마다 이 계약으로 대상자 구간을 받아 대상자별 풀에
    저장·누적한다. 클론은 쌓인 풀을 /voice/clone(samples[])에 넘겨 별도로 수행한다.
    """

    # 대상자 구간을 잘라낼 원본 녹음(presigned URL). 반환 구간에 이 URL이 실린다.
    audio_url: HttpUrl
    # 화자 라벨이 붙은 전사 구간들.
    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)
    # 화자 식별로 확정된 대상자 라벨. 미확정(None)이면 이 통화는 풀에 기여분이 없다(빈 목록).
    subject_speaker_label: str | None = Field(default=None, max_length=120)


class SubjectSampleCollectionResponse(ApiModel):
    # 이 통화의 대상자 발화 구간(상한·선별 없이 전부). 상한·선별은 클론 시점에 적용.
    samples: list[VoiceSampleSegment] = Field(default_factory=list)


class VoiceCloneFromTranscriptRequest(ApiModel):
    """전사 결과에서 대상자 구간을 자동 수집해 클론하는 요청.

    /voice/clone은 호출자가 samples[]를 직접 조립해야 하지만, 이 계약은 전사 산출물
    (화자 라벨이 붙은 구간들) + 확정된 subjectSpeakerLabel을 넘기면 AI가 대상자 구간을
    골라 모아 클론한다. 여러 통화 누적은 후속(재료 풀)이고, 지금은 녹음 1건 단위다.
    """

    # 클론 음성 이름(대상자 식별용).
    name: str = Field(min_length=1, max_length=200)
    # 대상자 구간을 잘라낼 원본 녹음(presigned URL).
    audio_url: HttpUrl
    # 화자 라벨이 붙은 전사 구간들.
    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)
    # 화자 식별로 확정된 대상자 라벨. 미확정(None)이면 클론을 진행하지 않는다.
    subject_speaker_label: str | None = Field(default=None, max_length=120)


class VoiceCloneResponse(ApiModel):
    voice_id: str
    provider: str


class SpeechSynthesisRequest(ApiModel):
    text: str = Field(min_length=1, max_length=20_000)
    # 대상자 클론 음성 id. 미지정 시 ELEVENLABS_DEFAULT_VOICE_ID로 폴백.
    voice_id: str | None = Field(default=None, max_length=200)
    # 말하는 속도(0.7~1.2, 1.0=기본). 미지정 시 서버 ELEVENLABS_SPEED로 폴백.
    speed: float | None = Field(default=None, ge=0.7, le=1.2)
