from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.api.dependencies import SettingsDependency, require_internal_request
from app.pipeline.voice.service import (
    clone_voice_from_sample,
    synthesize_speech,
    transcribe_voice_message,
)
from app.schemas.voice import (
    SpeechSynthesisRequest,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceTranscriptionResponse,
)


router = APIRouter(tags=["voice"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/transcriptions", response_model=VoiceTranscriptionResponse)
async def create_voice_transcription(
    _authorized: InternalRequest,
    settings: SettingsDependency,
    audio_file: Annotated[UploadFile, File()],
    language: Annotated[str, Form()] = "ko",
) -> VoiceTranscriptionResponse:
    """채팅 음성 메시지 받아쓰기 — 멀티파트 audio_file → 텍스트."""
    content = await audio_file.read()
    return transcribe_voice_message(
        content, audio_file.filename or "audio.wav", language=language, settings=settings
    )


@router.post("/speech")
def create_speech(
    request: SpeechSynthesisRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> Response:
    """페르소나 답변 음성 합성 — text(+voiceId) → 고인 목소리 audio/mpeg."""
    result = synthesize_speech(request, settings=settings)
    return Response(content=result.audio, media_type=result.content_type)


@router.post("/clone", response_model=VoiceCloneResponse)
def create_voice_clone(
    request: VoiceCloneRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> VoiceCloneResponse:
    """고인 목소리 샘플 클립 → 클론 음성 등록 → voiceId(대상자별 TTS 음성)."""
    return clone_voice_from_sample(request, settings=settings)
