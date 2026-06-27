from uuid import UUID

from pydantic import Field

from app.schemas.common import ApiModel


class TranscriptionRequest(ApiModel):
    job_id: UUID
    recording_id: UUID
    language: str = Field(default="ko", min_length=2, max_length=20)
    speaker_diarization: bool = True
    glossary: list[str] = Field(default_factory=list, max_length=500)


class TranscriptSegment(ApiModel):
    id: UUID | None = None
    segment_index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_label: str = Field(default="unknown", max_length=120)
    transcript_text: str = Field(min_length=1, max_length=20_000)
    confidence: float | None = Field(default=None, ge=0, le=1)


class TranscriptionResponse(ApiModel):
    segments: list[TranscriptSegment]
    provider: str
    model: str
