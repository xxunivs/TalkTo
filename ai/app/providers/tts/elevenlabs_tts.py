import httpx

from app.core.errors import TTSProviderError
from app.providers.tts.interface import TTSResult

ELEVENLABS_BASE = "https://api.elevenlabs.io"
_DEFAULT_MODEL = "eleven_multilingual_v2"


class ElevenLabsTTSProvider:
    """ElevenLabs text-to-speech (MVP 검증 방식 이식). voice_id = 대상자 클론 음성."""

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.model_id = model_id or _DEFAULT_MODEL
        self.timeout_seconds = timeout_seconds

    def synthesize(self, text: str, *, voice_id: str) -> TTSResult:
        url = f"{ELEVENLABS_BASE}/v1/text-to-speech/{voice_id}"
        try:
            response = httpx.post(
                url,
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": self.model_id,
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise TTSProviderError(f"TTS 요청 실패: {error}") from error
        if response.status_code != 200:
            raise TTSProviderError(
                f"ElevenLabs TTS 오류 {response.status_code}: {response.text[:200]}"
            )
        return TTSResult(
            audio=response.content,
            content_type="audio/mpeg",
            provider="elevenlabs",
            model=self.model_id,
        )
