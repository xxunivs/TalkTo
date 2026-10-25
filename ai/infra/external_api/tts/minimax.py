"""MiniMax 목소리 provider(예전 목소리): 합성 + 지우기.

새 목소리는 더 이상 MiniMax 에서 만들지 않는다(Fish Audio 에서 만든다). 예전에 만든 목소리
(voice_id 가 "TalkTo" 로 시작)는 다시 만들기 전까지 여기서 합성하고, 다시 만든 뒤에 지운다.
"""

import logging

import httpx

from infra.error import TTSProviderError
from infra.external_api.tts.elevenlabs import TTSResult

logger = logging.getLogger(__name__)

MINIMAX_BASE = "https://api.minimax.io"
_DEFAULT_MODEL = "speech-2.8-turbo"
# 우리가 만든 MiniMax voice_id 는 모두 이 글자로 시작한다. 합성할 때 이것으로 MiniMax 목소리인지
# 알아본다(ElevenLabs voice_id 는 영숫자 20자, Fish 는 "fish_" 로 시작해 겹치지 않는다).
VOICE_ID_PREFIX = "TalkTo"
# 우리 감정 이름 → MiniMax 감정. neutral 은 보내지 않는다(MiniMax 가 글을 보고 정한다).
# MiniMax 에는 angry·fearful·disgusted 도 있지만 우리 쪽에서 고를 수 없다.
# Fish 용 이름 warm·worried 는 MiniMax 에 맞는 감정이 없어 None(없음)으로 본다 → 서버 기본값 또는 MiniMax 가 정한다.
_EMOTIONS = {"calm": "calm", "happy": "happy", "sad": "sad", "surprised": "surprised"}


def is_minimax_voice(voice_id: str) -> bool:
    return voice_id.startswith(VOICE_ID_PREFIX)


def _check(response: httpx.Response, what: str) -> dict:
    """HTTP 200 이어도 base_resp.status_code 가 0 이 아니면 실패다."""
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
        # BE 가 45초에 끊는다. 그보다 길게 기다리면 합성 값만 나가고 소리는 버려진다(Fish 는 40초 마감)
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
        # stability·similarity_boost·style 은 ElevenLabs 값이라 여기선 쓰지 않는다.
        voice_setting: dict = {"voice_id": voice_id, "speed": speed, "vol": 1, "pitch": 0}
        # 대답마다 고른 감정이 먼저, 없으면 서버 기본(MINIMAX_EMOTION), 그것도 없으면 MiniMax 가 정한다
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
            # +면 부드럽게, -면 힘 있게(-100~100)
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
        """복제한 목소리를 MiniMax 에서 지운다(대화 상대를 지울 때·목소리를 다시 만들 때)."""
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
