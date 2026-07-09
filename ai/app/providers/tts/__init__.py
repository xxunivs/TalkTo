from app.core.config import Settings
from app.core.errors import ProviderNotConfiguredError
from app.providers.tts.elevenlabs_tts import ElevenLabsTTSProvider
from app.providers.tts.interface import TTSProvider, TTSResult

__all__ = ["TTSProvider", "TTSResult", "create_tts_provider"]


def create_tts_provider(settings: Settings) -> TTSProvider:
    if settings.tts_provider == "elevenlabs":
        if settings.elevenlabs_api_key is None:
            raise ProviderNotConfiguredError(
                "ELEVENLABS_API_KEY is required for the elevenlabs TTS provider."
            )
        return ElevenLabsTTSProvider(
            api_key=settings.elevenlabs_api_key.get_secret_value(),
            model_id=settings.elevenlabs_model,
        )
    raise ProviderNotConfiguredError(f"Unknown TTS provider: {settings.tts_provider}")
