from typing import Literal
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
