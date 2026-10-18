import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from infra.error import TTSProviderError
from infra.external_api.elevenlabs_records import forget_speech


@dataclass
class TTSResult:
    audio: bytes
    content_type: str
    provider: str
    model: str


class TTSProvider(Protocol):
    """대상자 목소리 provider 경계: 합성(TTS) + 목소리 지우기.

    새 목소리 만들기(clone_voice)는 Fish Audio 만 한다. ElevenLabs·MiniMax 는 예전에 만든 목소리를
    다시 만들기 전까지 합성하고, 다시 만든 뒤에 지운다.
    """

    name: str

    def synthesize(
        self, text: str, *, voice_id: str, speed: float = 1.0,
        stability: float = 0.3, similarity_boost: float = 0.75, style: float = 0.0,
        emotion: str | None = None,
    ) -> TTSResult: ...

    def delete_voice(self, voice_id: str) -> None: ...

logger = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io"
_DEFAULT_MODEL = "eleven_multilingual_v2"
# 모델마다 받는 voice_settings 가 다르다. v4 계열에는 style·speed 가 없다(문서·실측 확인).
# 모르는 모델은 v2 처럼 다 보낸다.
_V4_SETTINGS = ("stability", "similarity_boost")
_ALL_SETTINGS = ("stability", "similarity_boost", "style", "speed")


def _is_v4(model_id: str) -> bool:
    return model_id.startswith("eleven_v4")


def allowed_settings(model_id: str) -> tuple[str, ...]:
    return _V4_SETTINGS if _is_v4(model_id) else _ALL_SETTINGS


def v4_ready(voice: dict) -> bool:
    """이 목소리로 v4 합성을 해도 되는가. 즉시 복제 목소리(IVC, cloned)만 그렇다.

    IVC 는 따로 학습하는 단계가 없다. 예전 똑 닮은 목소리(PVC, professional)를 포함해 그 밖의 종류는
    다시 만들기 전까지 v2 로 합성한다.
    """
    return voice.get("category") == "cloned"


class ElevenLabsTTSProvider:
    """ElevenLabs text-to-speech. voice_id = 대상자 클론 음성."""

    name = "elevenlabs"
    _v4_cache: dict[str, bool] = {}

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
        """이 목소리에 쓸 모델. v4 를 쓰라고 했어도 준비 안 된 목소리는 v2 로 되돌린다."""
        if not _is_v4(self.model_id):
            return self.model_id
        if voice_id not in self._v4_ok:
            try:
                response = httpx.get(
                    f"{ELEVENLABS_BASE}/v1/voices/{voice_id}",
                    headers={"xi-api-key": self.api_key},
                    timeout=self.timeout_seconds,
                )
                voice = response.json() if response.status_code == 200 else {}
            except httpx.HTTPError:
                voice = {}
            ready = v4_ready(voice)
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
        # ElevenLabs 계정에 남는 대답 읽기 기록(대답 글·음성)을 바로 지운다
        forget_speech(self.api_key, voice_id=voice_id, headers=response.headers)
        return TTSResult(
            audio=response.content,
            content_type="audio/mpeg",
            provider="elevenlabs",
            model=model_id,
        )

    def delete_voice(self, voice_id: str) -> None:
        """목소리를 ElevenLabs 에서 지운다(대화 상대를 지울 때·목소리를 다시 만든 뒤).

        처음 만든 목소리(IVC)·예전 똑 닮은 목소리(PVC) 모두 같은 API 다(PVC 면 슬롯을 돌려받는다).
        """
        try:
            response = httpx.delete(
                f"{ELEVENLABS_BASE}/v1/voices/{voice_id}",
                headers={"xi-api-key": self.api_key},
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise TTSProviderError(f"목소리 지우기 요청 실패: {error}") from error
        if response.status_code >= 400:
            raise TTSProviderError(
                f"ElevenLabs 목소리 지우기 오류 {response.status_code}: {response.text[:200]}"
            )
        self._v4_ok.pop(voice_id, None)
        logger.info("elevenlabs voice deleted voice_id=%s", voice_id)
