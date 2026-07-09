from pydantic import Field

from app.schemas.common import ApiModel


class VoiceTranscriptionResponse(ApiModel):
    text: str
    provider: str
    model: str


class SpeechSynthesisRequest(ApiModel):
    text: str = Field(min_length=1, max_length=20_000)
    # 대상자 클론 음성 id. 미지정 시 ELEVENLABS_DEFAULT_VOICE_ID로 폴백.
    voice_id: str | None = Field(default=None, max_length=200)
    # 말하는 속도(0.7~1.2, 1.0=기본). 미지정 시 서버 ELEVENLABS_SPEED로 폴백.
    speed: float | None = Field(default=None, ge=0.7, le=1.2)
