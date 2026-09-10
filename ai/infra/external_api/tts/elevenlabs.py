from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from infra.error import TTSProviderError


@dataclass
class TTSResult:
    audio: bytes
    content_type: str
    provider: str
    model: str


class TTSProvider(Protocol):
    """대상자 목소리 provider 경계: 합성(TTS) + 샘플로 클론 등록."""

    def synthesize(
        self, text: str, *, voice_id: str, speed: float = 1.0,
        stability: float = 0.3, similarity_boost: float = 0.75, style: float = 0.0,
    ) -> TTSResult: ...

    def clone_voice(self, name: str, audio_path: Path) -> str: ...

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

    def synthesize(
        self, text: str, *, voice_id: str, speed: float = 1.0,
        stability: float = 0.3, similarity_boost: float = 0.75, style: float = 0.0,
    ) -> TTSResult:
        url = f"{ELEVENLABS_BASE}/v1/text-to-speech/{voice_id}"
        try:
            response = httpx.post(
                url,
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": self.model_id,
                    # 값은 대상자마다 다르다. 부르는 쪽이 정하고, 여기 기본값은
                    # 아무도 안 정했을 때만 쓰인다.
                    "voice_settings": {
                        "stability": stability,
                        "similarity_boost": similarity_boost,
                        "style": style,
                        "speed": speed,
                    },
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

    def clone_voice(self, name: str, audio_path: Path) -> str:
        """음성 샘플로 클론 음성을 등록하고 voice_id를 돌려준다(ElevenLabs add-voice)."""
        try:
            with audio_path.open("rb") as audio_file:
                response = httpx.post(
                    f"{ELEVENLABS_BASE}/v1/voices/add",
                    headers={"xi-api-key": self.api_key},
                    data={"name": name},
                    files={"files": (audio_path.name, audio_file)},
                    timeout=self.timeout_seconds,
                )
        except httpx.HTTPError as error:
            raise TTSProviderError(f"음성 클론 요청 실패: {error}") from error
        if response.status_code != 200:
            raise TTSProviderError(
                f"ElevenLabs 클론 오류 {response.status_code}: {response.text[:200]}"
            )
        voice_id = response.json().get("voice_id")
        if not voice_id:
            raise TTSProviderError("클론 응답에 voice_id가 없다.")
        return str(voice_id)
