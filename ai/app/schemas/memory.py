from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import ApiModel
from app.schemas.context import SubjectContext


class PersistedTranscriptSegment(ApiModel):
    id: UUID
    segment_index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_label: str = Field(default="unknown", max_length=120)
    transcript_text: str = Field(min_length=1, max_length=20_000)


class MemorySegmentExtractionRequest(ApiModel):
    job_id: UUID
    recording_id: UUID
    transcript_segments: list[PersistedTranscriptSegment] = Field(min_length=1)
    subject_context: SubjectContext | None = None
    # 대상자 화자 라벨. 미지정 시 발화량이 가장 많은 화자로 자동 판정.
    subject_speaker_label: str | None = Field(default=None, max_length=120)
    # 통화 상대 이름(업로드 시 선택). 있으면 대상자 아닌 화자를 이 이름으로 확정 귀속.
    conversation_partner_name: str | None = Field(default=None, max_length=120)


class MemorySegment(ApiModel):
    segment_index: int = Field(ge=0)
    source_transcript_segment_ids: list[UUID] = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_label: str = Field(default="unknown", max_length=120)
    memory_text: str = Field(min_length=1, max_length=20_000)
    confidence: Literal["confirmed", "inferred"]
    importance_score: int = Field(ge=1, le=10)
    tags: list[str] = Field(default_factory=list)
    related_people: list[str] = Field(default_factory=list)
    sensitivity_flags: list[str] = Field(default_factory=list)


class MemorySegmentExtractionResponse(ApiModel):
    segments: list[MemorySegment]
    provider: str
    model: str
