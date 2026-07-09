"""녹음 분석 통합 엔드포인트 계약 — 한 번의 호출로 3-A 기억 + 요약 + 태그 +
말투(④) + 안전플래그를 반환한다.

인물·민감·3-A·요약·④를 각각 별도 호출하면 공통 분석(인물·민감·3-A)이 중복되므로,
녹음 1건을 한 번에 분석하는 단일 계약으로 묶는다. reflection 산출(personaMaterials)은
녹음 여러 건 누적이 필요한 P2라 이 응답에 포함하지 않는다.
"""

from uuid import UUID

from pydantic import Field

from app.schemas.common import ApiModel
from app.schemas.context import SubjectContext
from app.schemas.memory import MemorySegment, PersistedTranscriptSegment


class RecordingAnalysisRequest(ApiModel):
    job_id: UUID
    recording_id: UUID
    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)
    subject_context: SubjectContext | None = None
    # 대상자 화자 라벨. 미지정 시 발화량이 가장 많은 화자로 자동 판정.
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
