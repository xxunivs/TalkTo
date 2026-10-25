"""말로 답하기 짧은 녹음(3분까지) -> 글 (ElevenLabs Scribe, 한국어 고정, 녹음과 Scribe 기록 즉시 삭제)"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from app.error import NoSpeechError, ProviderNotConfiguredError
from app.external_api.stt.elevenlabs_scribe import (
    ElevenLabsScribeProvider,
    STTProvider,
    strip_non_speech_notes,
)
from app.schema.voice import AnswerTranscriptionResponse
from app.setting import Settings
from app.temp_files import scratch_dir

logger = logging.getLogger(__name__)

# BE 전송 한도 6MB, 3분 (talkto_api_server transcriptions.MAX_BYTES) 대비 여유, 초과 시 413
ANSWER_AUDIO_MAX_BYTES = 8 * 1024 * 1024
# BE 대기 90초(ai.ANSWER_TRANSCRIBE_TIMEOUT) 이내
ANSWER_STT_TIMEOUT_SECONDS = 75.0
ANSWER_LANGUAGE = "ko"
_SUFFIX = re.compile(r"\.[a-z0-9]{1,5}")


def answer_stt_provider(settings: Settings) -> STTProvider:
    """말로 답하기 STT 제공자 (STT_PROVIDER 무관, 항상 ElevenLabs Scribe)"""
    if settings.elevenlabs_api_key is None:
        raise ProviderNotConfiguredError("ELEVENLABS_API_KEY is required for answer transcription.")
    return ElevenLabsScribeProvider(
        api_key=settings.elevenlabs_api_key.get_secret_value(),
        model_id=settings.elevenlabs_stt_model,
        timeout_seconds=ANSWER_STT_TIMEOUT_SECONDS,
    )


def _suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if _SUFFIX.fullmatch(suffix) else ".webm"


def transcribe_answer(
    audio: bytes,
    filename: str,
    *,
    settings: Settings,
    provider: STTProvider | None = None,
) -> AnswerTranscriptionResponse:
    """녹음 -> 받아쓴 글 (말소리 없으면 NoSpeechError, 422 NO_SPEECH)"""
    stt = provider or answer_stt_provider(settings)
    handle, name = tempfile.mkstemp(
        prefix="answer_", suffix=_suffix(filename), dir=scratch_dir(settings)
    )
    path = Path(name)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(audio)
        result = stt.transcribe(path, language=ANSWER_LANGUAGE, speaker_diarization=False)
    finally:
        # 가족 녹음 즉시 삭제 (실패 시 포함)
        path.unlink(missing_ok=True)
    text = strip_non_speech_notes(" ".join(seg.transcript_text for seg in result.segments))
    if not text:
        raise NoSpeechError()
    return AnswerTranscriptionResponse(text=text, provider=result.provider, model=result.model)
