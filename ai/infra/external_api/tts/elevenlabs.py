import logging
import time
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
        emotion: str | None = None,
    ) -> TTSResult: ...

    def clone_voice(self, name: str, audio_path: Path) -> str: ...

logger = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io"
_DEFAULT_MODEL = "eleven_multilingual_v2"
# 모델마다 받는 voice_settings 가 다르다. v4 계열에는 style·speed 가 없다(문서·실측 확인).
# 모르는 모델은 v2 처럼 다 보낸다.
_V4_SETTINGS = ("stability", "similarity_boost")
_NOT_READY_TTL = 30 * 60
_ALL_SETTINGS = ("stability", "similarity_boost", "style", "speed")


def _is_v4(model_id: str) -> bool:
    return model_id.startswith("eleven_v4")


def allowed_settings(model_id: str) -> tuple[str, ...]:
    return _V4_SETTINGS if _is_v4(model_id) else _ALL_SETTINGS


def v4_ready(voice: dict, model_id: str) -> bool:
    """이 목소리로 v4 합성을 해도 되는가.

    PVC(professional)는 My Voices 에서 v4 '+' 로 추가 학습해야 v4 에서 제 목소리가 난다.
    학습 상태(fine_tuning.state)에 v4 계열 fine_tuned 가 있어야 한다. IVC(cloned)는 따로
    학습하는 단계가 없다. 그 밖의 종류는 확인하지 못했으니 쓰지 않는다.
    """
    category = voice.get("category")
    if category == "cloned":
        return True
    if category == "professional":
        state = (voice.get("fine_tuning") or {}).get("state") or {}
        return any(
            value == "fine_tuned" and (key == model_id or key.startswith("eleven_v4"))
            for key, value in state.items()
        )
    return False


class ElevenLabsTTSProvider:
    """ElevenLabs text-to-speech. voice_id = 대상자 클론 음성."""

    name = "elevenlabs"
    _v4_cache: dict[str, bool] = {}
    _v4_checked_at: dict[str, float] = {}

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
        # voice_id → v4 로 합성해도 되는지. 목소리 학습 상태는 자주 안 바뀌어 프로세스 동안 둔다.
        # provider 는 요청마다 새로 만들어서, 인스턴스가 아니라 클래스에 둔다(인스턴스에 두면
        # 합성마다 조회가 한 번씩 더 붙어 약 0.7초 늦어진다).
        self._v4_ok = ElevenLabsTTSProvider._v4_cache

    def model_for(self, voice_id: str) -> str:
        """이 목소리에 쓸 모델. v4 를 쓰라고 했어도 준비 안 된 목소리는 v2 로 되돌린다.

        '아직 안 됨'은 30분만 믿는다. 정밀 목소리(PVC)는 v4 학습이 나중에 끝날 수 있다.
        """
        if not _is_v4(self.model_id):
            return self.model_id
        checked = ElevenLabsTTSProvider._v4_checked_at.get(voice_id, 0.0)
        if self._v4_ok.get(voice_id) is False and time.monotonic() - checked > _NOT_READY_TTL:
            # 여러 요청이 동시에 지울 수 있어 없는 열쇠도 괜찮게(pop)
            self._v4_ok.pop(voice_id, None)
        if voice_id not in self._v4_ok:
            ElevenLabsTTSProvider._v4_checked_at[voice_id] = time.monotonic()
            try:
                response = httpx.get(
                    f"{ELEVENLABS_BASE}/v1/voices/{voice_id}",
                    headers={"xi-api-key": self.api_key},
                    timeout=self.timeout_seconds,
                )
                voice = response.json() if response.status_code == 200 else {}
            except httpx.HTTPError:
                voice = {}
            ready = v4_ready(voice, self.model_id)
            self._v4_ok[voice_id] = ready
            if not ready:
                logger.warning(
                    "voice not ready for %s (category=%s) — falling back to %s",
                    self.model_id, voice.get("category"), _DEFAULT_MODEL,
                )
        return self.model_id if self._v4_ok.get(voice_id) else _DEFAULT_MODEL

    def synthesize(
        self, text: str, *, voice_id: str, speed: float = 1.0,
        stability: float = 0.3, similarity_boost: float = 0.75, style: float = 0.0,
        emotion: str | None = None,
    ) -> TTSResult:
        # emotion 은 받지만 쓰지 않는다. v4 는 글을 보고 감정을 스스로 싣는다.
        url = f"{ELEVENLABS_BASE}/v1/text-to-speech/{voice_id}"
        model_id = self.model_for(voice_id)
        values = {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "style": style,
            "speed": speed,
        }
        settings = {key: values[key] for key in allowed_settings(model_id)}
        try:
            response = httpx.post(
                url,
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": model_id,
                    # 값은 대상자마다 다르다. 부르는 쪽이 정하고, 여기 기본값은
                    # 아무도 안 정했을 때만 쓰인다. 모델이 받지 않는 값은 뺀다.
                    "voice_settings": settings,
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
            model=model_id,
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
