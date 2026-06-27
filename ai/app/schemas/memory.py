from uuid import UUID

from pydantic import Field

from app.schemas.common import ApiModel


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


class MemorySegment(ApiModel):
    segment_index: int = Field(ge=0)
    source_transcript_segment_ids: list[UUID] = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_label: str = Field(default="unknown", max_length=120)
    memory_text: str = Field(min_length=1, max_length=20_000)
    confidence: float | None = Field(default=None, ge=0, le=1)


class MemorySegmentExtractionResponse(ApiModel):
    segments: list[MemorySegment]
    provider: str
    model: str
