from infra.error import ProviderNotConfiguredError
from infra.external_api.tts.elevenlabs import (
    ElevenLabsTTSProvider,
    TTSProvider,
    TTSResult,
)
from infra.external_api.tts.fish import FishAudioTTSProvider, is_fish_voice
from infra.external_api.tts.minimax import MiniMaxTTSProvider, is_minimax_voice
from infra.setting import Settings

__all__ = [
    "TTSProvider",
    "TTSResult",
    "create_clone_provider",
    "create_tts_provider",
    "provider_for_voice",
    "wants_emotion_hint",
]


def wants_emotion_hint(voice_id: str | None) -> bool:
    """VOICE_EMOTION=inline 감정 이름 사용 여부 (MiniMax 전용, ElevenLabs v4 는 자체 감정, Fish 는 FISH_EMOTION)"""
    return bool(voice_id) and is_minimax_voice(voice_id)


def provider_for_voice(voice_id: str) -> str:
    """voice_id -> 제공사 (fish_ -> fish, TalkTo -> minimax, 그 외 -> elevenlabs)"""
    if is_fish_voice(voice_id):
        return "fish"
    if is_minimax_voice(voice_id):
        return "minimax"
    return "elevenlabs"


def create_tts_provider(
    settings: Settings, voice_id: str | None = None, provider: str | None = None
) -> TTSProvider:
    """TTS 제공사 선택 (voice_id -> provider -> TTS_PROVIDER 순)"""
    name = provider or settings.tts_provider
    if voice_id is not None:
        name = provider_for_voice(voice_id)
    if name == "fish":
        return _fish(settings)
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


def create_clone_provider(settings: Settings) -> FishAudioTTSProvider:
    """새 목소리 생성은 항상 Fish Audio (새 사용자 동의 범위가 Fish 뿐이라 전환 스위치 없음)"""
    return _fish(settings)


def _fish(settings: Settings) -> FishAudioTTSProvider:
    if settings.fish_audio_api_key is None:
        raise ProviderNotConfiguredError(
            "FISH_AUDIO_API_KEY is required for the fish TTS provider."
        )
    return FishAudioTTSProvider(
        api_key=settings.fish_audio_api_key.get_secret_value(),
        emotion_mode=getattr(settings, "fish_emotion", "off"),
        happy_temperature=getattr(settings, "fish_happy_temperature", None),
    )
