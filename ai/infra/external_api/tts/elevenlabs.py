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
    """대상자 목소리 제공사 인터페이스: 합성, 삭제 (새 목소리 생성은 Fish Audio 전용)"""

    name: str

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        speed: float = 1.0,
        stability: float = 0.3,
        similarity_boost: float = 0.75,
        style: float = 0.0,
        emotion: str | None = None,
    ) -> TTSResult: ...

    def delete_voice(self, voice_id: str) -> None: ...


logger = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io"
_DEFAULT_MODEL = "eleven_multilingual_v2"
# 모델별 voice_settings (v4 계열은 style, speed 없음: 문서, 실측 확인, 모르는 모델은 전부 전송)
_V4_SETTINGS = ("stability", "similarity_boost")
_ALL_SETTINGS = ("stability", "similarity_boost", "style", "speed")


def _is_v4(model_id: str) -> bool:
    return model_id.startswith("eleven_v4")


def allowed_settings(model_id: str) -> tuple[str, ...]:
    return _V4_SETTINGS if _is_v4(model_id) else _ALL_SETTINGS


def v4_ready(voice: dict) -> bool:
    """v4 합성 가능 여부 (IVC 즉시 복제 목소리만, PVC 등 그 외는 v2)"""
    return voice.get("category") == "cloned"


class ElevenLabsTTSProvider:
    """ElevenLabs TTS (voice_id 는 대상자 클론 음성)"""

    name = "elevenlabs"
    _v4_cache: dict[str, bool] = {}

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str | None = None,
        # BE 45초 한도보다 짧게 (초과 시 합성 비용만 나가고 소리는 버려짐, Fish 는 40초)
        timeout_seconds: float = 35.0,
    ) -> None:
        self.api_key = api_key
        self.model_id = model_id or _DEFAULT_MODEL
        self.timeout_seconds = timeout_seconds
        # voice_id -> v4 가능 여부, 프로세스 수명 캐시
        # provider 는 요청마다 새로 생성되어 클래스에 보관 (인스턴스 보관 시 합성마다 조회로 약 0.7초 지연)
        self._v4_ok = ElevenLabsTTSProvider._v4_cache

    def model_for(self, voice_id: str) -> str:
        """목소리별 합성 모델 (v4 미준비 목소리는 v2)"""
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
                    self.model_id,
                    voice.get("category"),
                    _DEFAULT_MODEL,
                )
        return self.model_id if self._v4_ok.get(voice_id) else _DEFAULT_MODEL

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        speed: float = 1.0,
        stability: float = 0.3,
        similarity_boost: float = 0.75,
        style: float = 0.0,
        emotion: str | None = None,
    ) -> TTSResult:
        # emotion 미사용 (v4 는 글 기반 자체 감정)
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
                    # 대상자별 값은 호출자가 결정, 모델이 받지 않는 값은 제외
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
        # ElevenLabs 계정의 대답 읽기 기록(글, 음성) 즉시 삭제
        forget_speech(self.api_key, voice_id=voice_id, headers=response.headers)
        return TTSResult(
            audio=response.content,
            content_type="audio/mpeg",
            provider="elevenlabs",
            model=model_id,
        )

    def delete_voice(self, voice_id: str) -> None:
        """ElevenLabs 목소리 삭제 (IVC, PVC 공통 API, PVC 는 슬롯 반환)"""
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
