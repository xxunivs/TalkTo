"""채팅 음성 입출력 — 음성 메시지 STT(OpenAI) + 페르소나 답변 TTS(ElevenLabs).

녹음 분석용 전사(/analysis)와 별개: 여기는 가족이 페르소나에게 말한 짧은 음성 메시지를
받아쓰고(STT), 페르소나 답변을 고인 목소리로 합성(TTS)한다. MVP 검증 로직 이식.
"""

import io

from app.core.config import Settings
from app.core.errors import MissingVoiceError
from app.providers.llm import create_openai_client
from app.providers.tts import create_tts_provider
from app.providers.tts.interface import TTSResult
from app.schemas.voice import SpeechSynthesisRequest, VoiceTranscriptionResponse


def transcribe_voice_message(
    audio: bytes,
    filename: str,
    *,
    language: str,
    settings: Settings,
) -> VoiceTranscriptionResponse:
    client = create_openai_client(settings, "voice message stt")
    buffer = io.BytesIO(audio)
    buffer.name = filename or "audio.wav"
    response = client.audio.transcriptions.create(
        model=settings.openai_stt_model,
        file=buffer,
        language=language,
    )
    return VoiceTranscriptionResponse(
        text=(response.text or "").strip(),
        provider="openai",
        model=settings.openai_stt_model,
    )


def synthesize_speech(
    request: SpeechSynthesisRequest, *, settings: Settings
) -> TTSResult:
    voice_id = request.voice_id or settings.elevenlabs_default_voice_id
    if not voice_id:
        raise MissingVoiceError(
            "voiceId가 필요합니다 (요청의 voiceId 또는 ELEVENLABS_DEFAULT_VOICE_ID)."
        )
    provider = create_tts_provider(settings)
    return provider.synthesize(request.text, voice_id=voice_id)
