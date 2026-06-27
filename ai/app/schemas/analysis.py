from uuid import UUID

from pydantic import Field

from app.schemas.common import ApiModel


class AnalysisTextSegment(ApiModel):
    id: UUID
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_label: str = Field(default="unknown", max_length=120)
    text: str = Field(min_length=1, max_length=20_000)


class EnrichmentRequest(ApiModel):
    job_id: UUID
    recording_id: UUID
    subject_id: UUID
    segments: list[AnalysisTextSegment] = Field(min_length=1)


class RelatedPerson(ApiModel):
    name_or_label: str
    relationship: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence_segment_ids: list[UUID] = Field(default_factory=list)


class SafetyFlag(ApiModel):
    code: str
    severity: str
    restrict_persona_use: bool = False
    needs_review: bool = False
    evidence_segment_ids: list[UUID] = Field(default_factory=list)


class PersonaMaterial(ApiModel):
    material_type: str
    content: str
    confidence: float = Field(ge=0, le=1)
    evidence_segment_ids: list[UUID] = Field(default_factory=list)


class EnrichmentResponse(ApiModel):
    summary: str
    tags: list[str] = Field(default_factory=list)
    related_people: list[RelatedPerson] = Field(default_factory=list)
    speech_style: dict[str, str | list[str]] = Field(default_factory=dict)
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    persona_materials: list[PersonaMaterial] = Field(default_factory=list)
    provider: str
    model: str
