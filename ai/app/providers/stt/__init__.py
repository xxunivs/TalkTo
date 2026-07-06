from app.core.config import Settings
from app.core.errors import ProviderNotConfiguredError
from app.providers.stt.elevenlabs_scribe import ElevenLabsScribeProvider
from app.providers.stt.interface import STTProvider, STTResult

__all__ = ["STTProvider", "STTResult", "create_stt_provider"]


def create_stt_provider(settings: Settings) -> STTProvider:
    if settings.stt_provider == "elevenlabs":
        if settings.elevenlabs_api_key is None:
            raise ProviderNotConfiguredError(
                "ELEVENLABS_API_KEY is required for the elevenlabs STT provider."
            )
        return ElevenLabsScribeProvider(
            api_key=settings.elevenlabs_api_key.get_secret_value(),
            model_id=settings.elevenlabs_stt_model,
            timeout_seconds=settings.stt_timeout_seconds,
        )
    raise ProviderNotConfiguredError(f"Unknown STT provider: {settings.stt_provider}")
