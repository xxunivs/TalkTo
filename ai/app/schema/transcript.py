from typing import Literal
from uuid import UUID

from pydantic import Field, HttpUrl

from app.schema.field_rule import ApiModel
from app.schema.subject import IntakeContext, SubjectContext


class TranscriptionRequest(ApiModel):
    job_id: UUID
    recording_id: UUID
    audio_url: HttpUrl
    audio_mime_type: str | None = Field(default=None, max_length=100)
    mode: Literal["full", "preview"] = "full"
    language: str = Field(default="ko", min_length=2, max_length=20)
    speaker_diarization: bool = True
    glossary: list[str] = Field(default_factory=list, max_length=500)
    # 보정, 화자 식별 용어 자동 파생 (glossary 와 합집합)
    subject_context: SubjectContext | None = None
    intake_context: IntakeContext | None = None
    # 대상자 목소리 참조 샘플 presigned URL (있으면 ECAPA 매칭으로 subjectSpeakerLabel 확정)
    reference_voice_sample_url: HttpUrl | None = None


class TranscriptSegment(ApiModel):
    id: UUID | None = None
    segment_index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_label: str = Field(default="unknown", max_length=120)
    transcript_text: str = Field(min_length=1, max_length=20_000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    # 보정 결과 (원문과 분리, 교정 시에만)
    corrected_text: str | None = Field(default=None, max_length=20_000)
    needs_review: bool = False


class TranscriptionResponse(ApiModel):
    segments: list[TranscriptSegment]
    provider: str
    model: str
    # ECAPA 확정 대상자 화자 라벨 (참조 없음, 임계값 미달이면 null)
    subject_speaker_label: str | None = None
