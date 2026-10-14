"""MiniMax 목소리 provider: 샘플로 클론 등록 + 클론 목소리로 합성.

클론은 두 단계다. 샘플을 올려 file_id 를 받고(/v1/files/upload), 그 file_id 로 우리가 정한
voice_id 를 등록한다(/v1/voice_clone). 샘플은 10초~5분, 20MB 이하.

등록한 목소리는 7일 안에 한 번도 합성에 쓰지 않으면 지워진다. 그래서 등록 직후 짧은 문장을
한 번 합성해 둔다(클론 요금도 이 첫 사용 때 붙는다).
"""

import logging
import uuid
from pathlib import Path

import httpx

from infra.error import TTSProviderError
from infra.external_api.tts.elevenlabs import TTSResult

logger = logging.getLogger(__name__)

MINIMAX_BASE = "https://api.minimax.io"
_DEFAULT_MODEL = "speech-2.8-turbo"
# 우리가 만드는 voice_id 는 모두 이 글자로 시작한다. 합성할 때 이것으로 MiniMax 목소리인지
# 알아본다(ElevenLabs voice_id 는 영숫자 20자라 겹치지 않는다).
VOICE_ID_PREFIX = "TalkTo"
_ACTIVATE_TEXT = "응, 그래."
# 우리 감정 이름 → MiniMax 감정. neutral 은 보내지 않는다(MiniMax 가 글을 보고 정한다).
# MiniMax 에는 angry·fearful·disgusted 도 있지만 우리 쪽에서 고를 수 없다.
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
        timeout_seconds: float = 120,
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
        self, text: str, *, voice_id: str, speed: float = 1.0,
        stability: float = 0.3, similarity_boost: float = 0.75, style: float = 0.0,
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
            "audio_setting": {"sample_rate": 44100, "bitrate": 128000, "format": "mp3", "channel": 1},
        }
        if self.intensity:
            # +면 부드럽게, -면 힘 있게(-100~100)
            body["voice_modify"] = {"intensity": self.intensity}
        try:
            response = httpx.post(
                f"{MINIMAX_BASE}/v1/t2a_v2", headers=self._headers, json=body,
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

    def clone_voice(self, name: str, audio_path: Path) -> str:
        """샘플을 올리고 클론 목소리를 등록해 voice_id 를 돌려준다.

        name 은 다른 provider 와 맞춘 인자라 쓰지 않는다(대상자 이름이라 로그에도 남기지 않는다).
        """
        try:
            with audio_path.open("rb") as audio_file:
                uploaded = httpx.post(
                    f"{MINIMAX_BASE}/v1/files/upload",
                    headers=self._headers,
                    data={"purpose": "voice_clone"},
                    files={"file": (audio_path.name, audio_file)},
                    timeout=self.timeout_seconds,
                )
        except httpx.HTTPError as error:
            raise TTSProviderError(f"음성 샘플 업로드 실패: {error}") from error
        file_id = (_check(uploaded, "샘플 업로드").get("file") or {}).get("file_id")
        if not file_id:
            raise TTSProviderError("MiniMax 업로드 응답에 file_id 가 없다.")

        voice_id = f"{VOICE_ID_PREFIX}{uuid.uuid4().hex}"
        try:
            cloned = httpx.post(
                f"{MINIMAX_BASE}/v1/voice_clone",
                headers=self._headers,
                json={
                    "file_id": file_id,
                    "voice_id": voice_id,
                    # 잡음 제거는 끈다. 끈 채로 청취 검증했고, 우리 쪽 잡음 제거
                    # (noisereduce)도 귀로 들었을 때 나아지지 않았다.
                    "need_noise_reduction": False,
                    "need_volume_normalization": True,
                },
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise TTSProviderError(f"음성 클론 요청 실패: {error}") from error
        _check(cloned, "클론")
        logger.info("minimax voice cloned voice_id=%s", voice_id)
        # 7일 안에 쓰지 않으면 지워지므로 바로 한 번 쓴다. 실패해도 등록은 됐으니 넘어간다.
        try:
            self.synthesize(_ACTIVATE_TEXT, voice_id=voice_id)
        except TTSProviderError as error:
            logger.warning("minimax voice %s first use failed: %s", voice_id, error)
        return voice_id
