"""MiniMax 예전 목소리 합성, 삭제 (voice_id "TalkTo" 접두어, 새 목소리 생성 없음)"""

import logging

import httpx

from app.error import TTSProviderError
from app.external_api.tts.elevenlabs import TTSResult

logger = logging.getLogger(__name__)

MINIMAX_BASE = "https://api.minimax.io"
_DEFAULT_MODEL = "speech-2.8-turbo"
# MiniMax voice_id 접두어 (ElevenLabs 는 영숫자 20자, Fish 는 fish_ 라 겹치지 않음)
VOICE_ID_PREFIX = "TalkTo"
# 감정 이름 -> MiniMax 감정 (neutral 미전송, warm, worried 는 대응 없음 -> 서버 기본값 또는 MiniMax 판단)
_EMOTIONS = {"calm": "calm", "happy": "happy", "sad": "sad", "surprised": "surprised"}


def is_minimax_voice(voice_id: str) -> bool:
    return voice_id.startswith(VOICE_ID_PREFIX)


def _check(response: httpx.Response, what: str) -> dict:
    """응답 검사 (HTTP 200 이어도 base_resp.status_code 가 0 이 아니면 실패)"""
    if response.status_code != 200:
        raise TTSProviderError(f"MiniMax {what} 오류 {response.status_code}: {response.text[:200]}")
    body = response.json()
    base = body.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        raise TTSProviderError(
            f"MiniMax {what} 오류 {base.get('status_code')}: {base.get('status_msg', '')[:200]}"
        )
    return body


class MiniMaxTTSProvider:
    name = "minimax"

    def __init__(
        self,
        api_key: str,
        model_id: str | None = None,
        *,
        emotion: str | None = None,
        intensity: int = 0,
        # BE 45초 한도보다 짧게 (초과 시 합성 비용만 나가고 소리는 버려짐, Fish 는 40초)
        timeout_seconds: float = 35,
    ) -> None:
        self.api_key = api_key
        self.model_id = model_id or _DEFAULT_MODEL
        self.emotion = emotion or None
        self.intensity = intensity
        self.timeout_seconds = timeout_seconds

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

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
        # stability, similarity_boost, style 은 ElevenLabs 전용이라 미사용
        voice_setting: dict = {"voice_id": voice_id, "speed": speed, "vol": 1, "pitch": 0}
        # 감정 우선순위: 대답별 감정 -> MINIMAX_EMOTION -> MiniMax 판단
        chosen = _EMOTIONS.get(emotion or "") or self.emotion
        if chosen:
            voice_setting["emotion"] = chosen
        body: dict = {
            "model": self.model_id,
            "text": text,
            "stream": False,
            "language_boost": "Korean",
            "voice_setting": voice_setting,
            "audio_setting": {
                "sample_rate": 44100,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }
        if self.intensity:
            # + 부드럽게, - 힘 있게 (-100~100)
            body["voice_modify"] = {"intensity": self.intensity}
        try:
            response = httpx.post(
                f"{MINIMAX_BASE}/v1/t2a_v2",
                headers=self._headers,
                json=body,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise TTSProviderError(f"TTS 요청 실패: {error}") from error
        data = _check(response, "TTS").get("data") or {}
        if not data.get("audio"):
            raise TTSProviderError("MiniMax TTS 응답에 음성이 없다.")
        return TTSResult(
            audio=bytes.fromhex(data["audio"]),
            content_type="audio/mpeg",
            provider=self.name,
            model=self.model_id,
        )

    def delete_voice(self, voice_id: str) -> None:
        """MiniMax 복제 목소리 삭제"""
        try:
            response = httpx.post(
                f"{MINIMAX_BASE}/v1/delete_voice",
                headers=self._headers,
                json={"voice_type": "voice_cloning", "voice_id": voice_id},
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise TTSProviderError(f"목소리 지우기 요청 실패: {error}") from error
        _check(response, "목소리 지우기")
        logger.info("minimax voice deleted voice_id=%s", voice_id)
