"""녹음 분석 통합 계약 (기억, 요약, 태그, 말투, 안전 플래그 한 번에, 공통 분석 중복 방지)"""

from typing import Literal
from uuid import UUID

from pydantic import Field, HttpUrl

from infra.schema.field_rule import ApiModel
from infra.schema.memory import MemorySegment, PersistedTranscriptSegment
from infra.schema.subject import SubjectContext


class RecordingAnalysisRequest(ApiModel):
    job_id: UUID
    recording_id: UUID
    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)
    subject_context: SubjectContext | None = None
    # 대상자 화자 라벨 (ECAPA 확정값, 없으면 LLM 호칭, 맥락 폴백 -> None)
    subject_speaker_label: str | None = Field(default=None, max_length=120)
    # 통화 상대 이름 (선택, 대상자 아닌 화자를 이 이름으로 귀속)
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
    """전사문 호칭, 맥락 기반 대상자 화자 추천 요청 (ECAPA 참조 없을 때 화면 기본값, 확정은 사용자)"""

    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)
    subject_context: SubjectContext | None = None


class SubjectSpeakerResponse(ApiModel):
    # 등장 화자 라벨 중 하나 (근거 없으면 null)
    subject_speaker_label: str | None = None


class VoiceGroupRecording(ApiModel):
    recording_id: str = Field(min_length=1, max_length=100)
    # 녹음 오디오 presigned URL (화자별 목소리 지문용)
    audio_url: HttpUrl
    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)


class VoiceGroupRequest(ApiModel):
    """녹음 여러 개의 같은 사람 목소리 묶음 요청"""

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
    # 말한 시간 내림차순
    groups: list[VoiceGroup]
    # ecapa = 지문으로 녹음 간 묶음, per_recording = 지문 불가로 녹음별
    method: Literal["ecapa", "per_recording"]
