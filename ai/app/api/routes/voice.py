from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import require_internal_request
from app.core.errors import FeatureNotImplementedError
from app.schemas.voice import (
    SpeechSynthesisRequest,
    SpeechSynthesisResponse,
    VoiceTranscriptionRequest,
    VoiceTranscriptionResponse,
)


router = APIRouter(tags=["voice"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/transcriptions", response_model=VoiceTranscriptionResponse)
def create_voice_transcription(
    _request: VoiceTranscriptionRequest,
    _authorized: InternalRequest,
) -> VoiceTranscriptionResponse:
    raise FeatureNotImplementedError("Voice message transcription")


@router.post("/speech", response_model=SpeechSynthesisResponse)
def create_speech(
    _request: SpeechSynthesisRequest,
    _authorized: InternalRequest,
) -> SpeechSynthesisResponse:
    raise FeatureNotImplementedError("Speech synthesis")
