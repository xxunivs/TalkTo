from infra.setting import Settings
from infra.error import ProviderNotConfiguredError
from infra.external_api.tts.elevenlabs import (
    ElevenLabsTTSProvider,
    TTSProvider,
    TTSResult,
)
from infra.external_api.tts.minimax import MiniMaxTTSProvider, is_minimax_voice

__all__ = ["TTSProvider", "TTSResult", "create_tts_provider", "wants_emotion_hint"]


def wants_emotion_hint(voice_id: str | None) -> bool:
    """이 목소리가 대답마다 감정 이름을 받아 쓰는가.

    MiniMax 는 감정 이름을 받는다. ElevenLabs v4 는 글을 보고 스스로 감정을 싣기 때문에
    감정을 고르라는 지침을 넣지 않는다(괜히 넣으면 표시가 글에 섞여 나올 위험만 생긴다).
    """
    return bool(voice_id) and is_minimax_voice(voice_id)


def create_tts_provider(
    settings: Settings, voice_id: str | None = None, provider: str | None = None
) -> TTSProvider:
    """voice_id 를 주면 그 목소리를 만든 회사로, 안 주면(새 클론) provider → TTS_PROVIDER 순으로 고른다."""
    name = provider or settings.tts_provider
    if voice_id is not None:
        name = "minimax" if is_minimax_voice(voice_id) else "elevenlabs"
    if name == "minimax":
        if settings.minimax_api_key is None:
            raise ProviderNotConfiguredError(
                "MINIMAX_API_KEY is required for the minimax TTS provider."
            )
        return MiniMaxTTSProvider(
            api_key=settings.minimax_api_key.get_secret_value(),
            model_id=settings.minimax_model,
            emotion=settings.minimax_emotion,
            intensity=settings.minimax_intensity,
        )
    if name == "elevenlabs":
        if settings.elevenlabs_api_key is None:
            raise ProviderNotConfiguredError(
                "ELEVENLABS_API_KEY is required for the elevenlabs TTS provider."
            )
        return ElevenLabsTTSProvider(
            api_key=settings.elevenlabs_api_key.get_secret_value(),
            model_id=settings.elevenlabs_model,
        )
    raise ProviderNotConfiguredError(f"Unknown TTS provider: {name}")
