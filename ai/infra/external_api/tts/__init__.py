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
    """이 목소리가 VOICE_EMOTION=inline 일 때 대답마다 감정 이름을 받아 쓰는가.

    MiniMax(예전 목소리)만 이 지침을 받는다. ElevenLabs v4 는 글을 보고 스스로 감정을 싣기 때문에 넣지 않는다.
    Fish drama-3 는 따로 FISH_EMOTION 이 정한다(pipeline/s4_voice/b_emotion.voice_mode).
    """
    return bool(voice_id) and is_minimax_voice(voice_id)


def provider_for_voice(voice_id: str) -> str:
    """목소리를 만든 회사: "fish_…" → fish, "TalkTo…" → minimax(예전), 그 밖 → elevenlabs(예전 IVC·PVC)."""
    if is_fish_voice(voice_id):
        return "fish"
    if is_minimax_voice(voice_id):
        return "minimax"
    return "elevenlabs"


def create_tts_provider(
    settings: Settings, voice_id: str | None = None, provider: str | None = None
) -> TTSProvider:
    """voice_id 를 주면 그 목소리를 만든 회사로, 안 주면 provider → TTS_PROVIDER 순으로 고른다."""
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
    """새 목소리는 늘 Fish Audio 에서 만든다(BE 가 보낸 provider·TTS_PROVIDER 와 무관).

    다른 회사로 되돌리는 스위치를 두지 않는다. 새 사용자는 Fish 로 보내는 데만 동의한다.
    """
    return _fish(settings)


def _fish(settings: Settings) -> FishAudioTTSProvider:
    if settings.fish_audio_api_key is None:
        raise ProviderNotConfiguredError(
            "FISH_AUDIO_API_KEY is required for the fish TTS provider."
        )
    return FishAudioTTSProvider(
        api_key=settings.fish_audio_api_key.get_secret_value(),
        emotion_mode=getattr(settings, "fish_emotion", "off"),
    )
