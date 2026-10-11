"""녹음 분석 통합 엔드포인트 계약 — 한 번의 호출로 3-A 기억 + 요약 + 태그 +
말투(④) + 안전플래그를 반환한다.

인물·민감·3-A·요약·④를 각각 별도 호출하면 공통 분석(인물·민감·3-A)이 중복되므로,
녹음 1건을 한 번에 분석하는 단일 계약으로 묶는다. reflection 산출(personaMaterials)은
녹음 여러 건 누적이 필요한 P2라 이 응답에 포함하지 않는다.
"""

from uuid import UUID

from typing import Literal

from pydantic import Field, HttpUrl

from infra.schema.field_rule import ApiModel
from infra.schema.subject import SubjectContext
from infra.schema.memory import MemorySegment, PersistedTranscriptSegment


class RecordingAnalysisRequest(ApiModel):
    job_id: UUID
    recording_id: UUID
    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)
    subject_context: SubjectContext | None = None
    # 대상자 화자 라벨(ECAPA 성문으로 확정된 값). 미지정 시 전사문 호칭·맥락 LLM 폴백으로
    # 판정하고, 그래도 못 정하면 미확정(None)으로 진행한다(발화량 최다 추정은 폐기).
    subject_speaker_label: str | None = Field(default=None, max_length=120)
    # 통화 상대 이름(업로드 시 선택). 있으면 대상자 아닌 화자를 이 이름으로 확정 귀속.
    conversation_partner_name: str | None = Field(default=None, max_length=120)


class RecurringPhrase(ApiModel):
    phrase: str
    source_transcript_segment_ids: list[UUID] = Field(default_factory=list)


class AddressTerm(ApiModel):
    person: str
    term: str
    source_transcript_segment_ids: list[UUID] = Field(default_factory=list)


class EmotionalExpression(ApiModel):
    emotion: str
    expression: str
    source_transcript_segment_ids: list[UUID] = Field(default_factory=list)


class SpeechStyle(ApiModel):
    recurring_phrases: list[RecurringPhrase] = Field(default_factory=list)
    address_terms: list[AddressTerm] = Field(default_factory=list)
    sentence_patterns: list[str] = Field(default_factory=list)
    emotional_expressions: list[EmotionalExpression] = Field(default_factory=list)


class RecordingSafetyFlag(ApiModel):
    type: str  # health | familyConflict | asset | death | thirdParty
    description: str
    source_transcript_segment_ids: list[UUID] = Field(default_factory=list)


class RecordingAnalysisResponse(ApiModel):
    memory_segments: list[MemorySegment] = Field(default_factory=list)
    summary: str
    tags: list[str] = Field(default_factory=list)
    speech_style: SpeechStyle
    safety_flags: list[RecordingSafetyFlag] = Field(default_factory=list)
    provider: str
    model: str


class SubjectSpeakerRequest(ApiModel):
    """녹음 1건에서 누가 대상자인지 전사문 호칭·맥락으로 추천받는다.

    참조 목소리(ECAPA)가 없을 때 화자 고르기 화면에 미리 골라 둘 값이다. 확정은 사람이 한다.
    """

    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)
    subject_context: SubjectContext | None = None


class SubjectSpeakerResponse(ApiModel):
    # 등장한 화자 라벨 중 하나. 근거가 없으면 null.
    subject_speaker_label: str | None = None


class VoiceGroupRecording(ApiModel):
    recording_id: str = Field(min_length=1, max_length=100)
    # 녹음 오디오(presigned URL). 화자별 목소리 지문을 만들려고 내려받는다.
    audio_url: HttpUrl
    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)


class VoiceGroupRequest(ApiModel):
    """녹음 여러 개에 걸쳐 같은 사람의 목소리를 하나로 묶어 달라는 요청."""

    recordings: list[VoiceGroupRecording] = Field(min_length=1, max_length=50)


class VoiceGroupMember(ApiModel):
    recording_id: str
    speaker_label: str
    speech_ms: int


class VoiceGroup(ApiModel):
    group_index: int
    speech_ms: int
    members: list[VoiceGroupMember]


class VoiceGroupResponse(ApiModel):
    # 말한 시간이 긴 순서
    groups: list[VoiceGroup]
    # ecapa = 목소리 지문으로 녹음 사이를 묶음 / per_recording = 지문을 못 써서 녹음별로만
    method: Literal["ecapa", "per_recording"]
