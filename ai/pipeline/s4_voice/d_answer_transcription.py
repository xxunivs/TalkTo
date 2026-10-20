"""말로 답하기 — 가족이 질문 답·기억 채우기·고쳐 쓰기 칸에 말로 답한 짧은 녹음(3분까지)을 글로.

채팅 음성 메시지(a_voice.transcribe_voice_message, OpenAI)와 따로 ElevenLabs Scribe 로 받아쓴다
(대표 작업 지시). 한 사람의 답이라 화자 나누기는 하지 않고, 한국어로 고정한다.

녹음은 남기지 않는다: 받은 바이트를 임시 폴더(infra.scratch)의 파일 하나로 두고 받아쓰자마자 지운다.
ElevenLabs 계정에 남는 받아쓰기 기록(보낸 녹음·받아쓴 글)은 Scribe 제공자가 응답을 받은 바로 뒤에
지운다(elevenlabs_records.forget_transcript). 목소리 만들기에는 쓰지 않는다.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from infra.error import NoSpeechError, ProviderNotConfiguredError
from infra.external_api.stt.elevenlabs_scribe import (
    ElevenLabsScribeProvider,
    STTProvider,
    strip_non_speech_notes,
)
from infra.schema.voice import AnswerTranscriptionResponse
from infra.scratch import scratch_dir
from infra.setting import Settings

logger = logging.getLogger(__name__)

# BE 는 6MB·3분까지 보낸다(talkto_api_server transcriptions.MAX_BYTES). 넉넉히 받고, 넘으면 413.
ANSWER_AUDIO_MAX_BYTES = 8 * 1024 * 1024
# BE 는 90초까지 기다린다(ai.ANSWER_TRANSCRIBE_TIMEOUT). 3분 녹음은 보통 몇 초면 받아쓴다.
ANSWER_STT_TIMEOUT_SECONDS = 75.0
ANSWER_LANGUAGE = "ko"
_SUFFIX = re.compile(r"\.[a-z0-9]{1,5}")


def answer_stt_provider(settings: Settings) -> STTProvider:
    """말로 답하기 받아쓰기 제공자. 늘 ElevenLabs Scribe(STT_PROVIDER 와 상관없이)."""
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
    """녹음 → 받아쓴 글 한 덩어리. 말소리가 없으면 NoSpeechError(422 NO_SPEECH)."""
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
        # 가족의 목소리는 받아쓰자마자 지운다(실패해도)
        path.unlink(missing_ok=True)
    text = strip_non_speech_notes(" ".join(seg.transcript_text for seg in result.segments))
    if not text:
        raise NoSpeechError()
    return AnswerTranscriptionResponse(text=text, provider=result.provider, model=result.model)
