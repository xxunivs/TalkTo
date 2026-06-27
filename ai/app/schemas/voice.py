from uuid import UUID

from pydantic import Field

from app.schemas.common import ApiModel
from app.schemas.transcript import TranscriptSegment


class VoiceTranscriptionRequest(ApiModel):
    request_id: UUID
    language: str = Field(default="ko", min_length=2, max_length=20)


class VoiceTranscriptionResponse(ApiModel):
    text: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    provider: str
    model: str


class SpeechSynthesisRequest(ApiModel):
    request_id: UUID
    text: str = Field(min_length=1, max_length=20_000)
    voice_id: str = Field(min_length=1, max_length=500)
    output_format: str = Field(default="mp3", max_length=40)


class SpeechSynthesisResponse(ApiModel):
    content_type: str
    provider: str
    model: str
