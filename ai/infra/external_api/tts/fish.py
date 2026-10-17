"""Fish Audio 목소리 provider: 녹음으로 목소리 만들기(즉시 복제) + 그 목소리로 합성 + 지우기.

    만들기  POST   /model        multipart: type=tts, title, train_mode=fast, visibility=private, voices=<wav>
    상태    GET    /model/{id}   state: created → trained (또는 failed)
    합성    POST   /v1/tts       헤더 model: drama-3
    지우기  DELETE /model/{id}

우리 voice_id 는 "fish_" + Fish 모델 _id 다. 합성·지우기는 이 앞글자로 Fish 목소리인지 알아보고, Fish 에
보낼 때는 앞글자를 뗀다(ElevenLabs voice_id 는 영숫자 20자, MiniMax 는 "TalkTo…" 라 겹치지 않는다).

train_mode=fast 는 만들자마자 쓸 수 있어 201 응답이 대개 이미 trained 다. 아니면 잠깐 상태를 물어본다.
(docs.fish.audio · 2026-10-17 확인)
"""

import logging
import mimetypes
import re
import time
import uuid
from pathlib import Path

import httpx

from infra.error import (
    VOICE_PROVIDER_CREDITS,
    VOICE_PROVIDER_KEY,
    VOICE_PROVIDER_RATE_LIMIT,
    VOICE_PROVIDER_UNAVAILABLE,
    AppError,
    TTSProviderError,
    VoiceProviderError,
)
from infra.external_api.tts.elevenlabs import TTSResult

logger = logging.getLogger(__name__)

FISH_BASE = "https://api.fish.audio"
# 합성 모델. 헤더가 빠지거나 값이 틀리면 Fish 가 아무 말 없이 s2.1-pro 로 합성한다.
# 그래서 환경 변수로 열지 않고 여기 하나만 둔다(테스트가 정확한 값을 확인한다).
MODEL = "drama-3"
VOICE_ID_PREFIX = "fish_"
# 합성 한 번(다시 하기 포함)의 전체 시간. BE 의 AI 서버 호출 제한(45초)보다 짧게. BE 가 먼저 끊으면
# 합성 값만 나가고 소리는 버려진다. httpx 의 timeout 은 연결·읽기 같은 단계마다 따로 걸려(읽기는
# 조각 사이마다) 전체를 묶지 못하므로, 합성은 소리를 조각으로 받으며 이 마감을 직접 본다(_speak).
_TIMEOUT_SECONDS = 40.0
_CONNECT_SECONDS = 5.0
_CLONE_TIMEOUT_SECONDS = 120.0  # 1분 안팎 wav(약 5MB)를 올린다
# 합성만 한 번 더 해 본다. 목소리 만들기는 다시 보내면 목소리가 두 개 생긴다.
_RETRY_STATUS = frozenset({429, 503})
_RETRY_PAUSE_SECONDS = 1.0
_RETRY_MIN_LEFT_SECONDS = 10.0  # 남은 시간이 이보다 적으면 다시 해 봐야 마감 안에 끝나기 어렵다
_POLL_TRIES = 15
_POLL_SECONDS = 2.0
_POLL_TIMEOUT_SECONDS = 10.0  # 상태 묻기 한 번. 막혀도 기다림 전체가 길어지지 않게
_CODES = {
    401: VOICE_PROVIDER_KEY,
    403: VOICE_PROVIDER_KEY,
    402: VOICE_PROVIDER_CREDITS,
    429: VOICE_PROVIDER_RATE_LIMIT,
}
_MODEL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")

# drama-3 는 [ ] 안 글을 연기 지시·효과음으로, <whisper> 같은 꺾쇠 표시를 말투 지시로, <|speaker:0|> 를
# 화자 바꿈으로 읽는다. 대답에는 이런 표시를 쓰지 않는다(감정 표시 없이도 감정이 실렸다 — 대표님 청취).
_SPEAKER_TAG = re.compile(r"<\|[^|<>\n]*\|>")
_ANGLE_TAG = re.compile(r"<[^<>\n]{0,40}>")
_SQUARE = re.compile(r"\[[^\[\]\n]*\]")


def is_fish_voice(voice_id: str) -> bool:
    return voice_id.startswith(VOICE_ID_PREFIX)


def fish_model_id(voice_id: str) -> str:
    """우리 voice_id → Fish 모델 _id(앞글자를 뗀다). 앞글자가 없으면 그대로."""
    return voice_id.removeprefix(VOICE_ID_PREFIX)


def fish_plain_text(text: str) -> str:
    """drama-3 에 보낼 글: 지시로 읽히는 [ ]·< >·<| |> 표시를 지운다. 다 지워지면 원래 글을 보낸다.

    감정 표시([감정:sad], (감정: 슬픔) 등)는 부르는 쪽(synthesize_speech)이 strip_emotion_tags 로 먼저 지운다.
    """
    clean = _SQUARE.sub(" ", _ANGLE_TAG.sub(" ", _SPEAKER_TAG.sub(" ", text)))
    clean = re.sub(r"[ \t]+([.,!?…])", r"\1", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    clean = "\n".join(line.strip() for line in clean.splitlines()).strip()
    return clean or text


def _fail(response: httpx.Response, what: str) -> AppError:
    """Fish 오류 응답 → 코드가 있는 503(키·잔액·한도·장애) 또는 그 밖의 502."""
    status = response.status_code
    code = _CODES.get(status) or (VOICE_PROVIDER_UNAVAILABLE if status >= 500 else None)
    if code:
        logger.warning("fish %s rejected status=%s code=%s body=%s", what, status, code, response.text[:200])
        return VoiceProviderError(code, f"Fish Audio {what} 오류 {status}")
    return TTSProviderError(f"Fish Audio {what} 오류 {status}: {response.text[:200]}")


def _unavailable(what: str, error: httpx.HTTPError) -> VoiceProviderError:
    """시간 초과·연결 실패 → VOICE_PROVIDER_UNAVAILABLE."""
    return VoiceProviderError(
        VOICE_PROVIDER_UNAVAILABLE, f"Fish Audio {what} 요청 실패({type(error).__name__})"
    )


def _path_id(voice_id: str) -> str:
    """Fish 에 보낼 모델 id. Fish id 는 영숫자라 그 밖의 글자가 있거나 비었으면 보내지 않는다
    (비운 reference_id 로 합성하면 엉뚱한 목소리가 나온다)."""
    model_id = fish_model_id(voice_id)
    if not _MODEL_ID.fullmatch(model_id):
        raise TTSProviderError("Fish Audio 목소리 id 형식이 아니다.")
    return model_id


class FishAudioTTSProvider:
    name = "fish"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = _TIMEOUT_SECONDS,
        clone_timeout_seconds: float = _CLONE_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.clone_timeout_seconds = clone_timeout_seconds

    def _send(
        self, method: str, path: str, what: str, *, timeout: float,
        headers: dict[str, str] | None = None, **kwargs,
    ) -> httpx.Response:
        try:
            return httpx.request(
                method,
                f"{FISH_BASE}{path}",
                headers={"Authorization": f"Bearer {self.api_key}", **(headers or {})},
                timeout=timeout,
                **kwargs,
            )
        except httpx.HTTPError as error:  # 시간 초과·연결 실패
            raise _unavailable(what, error) from error

    def _speak(self, body: dict, deadline: float) -> tuple[httpx.Response, bytes]:
        """합성 요청 한 번. 소리를 조각으로 받다가 deadline(time.monotonic 기준)을 넘기면 끊는다.

        천천히 이어지는 응답도 마감 안에서 끝난다. 다만 조각 사이에서 아예 멈추면 그 한 번은
        읽기 제한(요청을 보낼 때 남은 시간)까지 기다린다. httpx 로는 읽는 중에 그 제한을
        줄일 수 없다.
        """
        left = max(deadline - time.monotonic(), 0.1)
        try:
            with httpx.stream(
                "POST",
                f"{FISH_BASE}/v1/tts",
                headers={"Authorization": f"Bearer {self.api_key}", "model": MODEL},
                json=body,
                timeout=httpx.Timeout(left, connect=min(_CONNECT_SECONDS, left)),
            ) as response:
                if response.status_code != 200:
                    response.read()  # 짧은 오류 JSON(로그·코드용)
                    return response, b""
                audio = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise VoiceProviderError(
                            VOICE_PROVIDER_UNAVAILABLE,
                            f"Fish Audio TTS 가 {self.timeout_seconds:g}초 안에 끝나지 않았다.",
                        )
                    audio += chunk
                return response, bytes(audio)
        except httpx.HTTPError as error:
            raise _unavailable("TTS", error) from error

    def synthesize(
        self, text: str, *, voice_id: str, speed: float = 1.0,
        stability: float = 0.3, similarity_boost: float = 0.75, style: float = 0.0,
        emotion: str | None = None,
    ) -> TTSResult:
        # speed·stability·similarity_boost·style 은 ElevenLabs 값, emotion 은 MiniMax 용이라 쓰지 않는다.
        # drama-3 는 속도·감정을 따로 주지 않은 채로 청취 비교를 통과했다.
        body = {
            "text": fish_plain_text(text),
            "reference_id": _path_id(voice_id),
            "format": "mp3",
            "sample_rate": 44100,
            "mp3_bitrate": 128,
            "latency": "normal",
        }
        deadline = time.monotonic() + self.timeout_seconds  # 다시 하기까지 이 안에서 끝낸다
        response, audio = self._speak(body, deadline)
        if response.status_code in _RETRY_STATUS:
            left = deadline - time.monotonic() - _RETRY_PAUSE_SECONDS
            if left >= _RETRY_MIN_LEFT_SECONDS:
                logger.info("fish tts busy status=%s, retrying once", response.status_code)
                time.sleep(_RETRY_PAUSE_SECONDS)
                response, audio = self._speak(body, deadline)
        if response.status_code != 200:
            raise _fail(response, "TTS")
        if not audio or "json" in response.headers.get("content-type", ""):
            raise TTSProviderError("Fish Audio TTS 응답에 음성이 없다.")
        return TTSResult(
            audio=audio,
            content_type="audio/mpeg",
            provider=self.name,
            model=MODEL,
        )

    def clone_voice(self, name: str, audio_path: Path) -> str:
        """녹음(1분 안팎 wav 하나)으로 목소리를 만들고 우리 voice_id("fish_" + _id)를 돌려준다.

        다시 만들 때는 새 목소리를 먼저 만들고 옛 목소리를 지운다. 그 사이 제목이 겹치지 않게 짧은 꼬리를
        붙인다. name 은 로그에 남기지 않는다. 실패해도 다시 보내지 않는다(목소리가 두 번 만들어진다).
        """
        data = {
            "type": "tts",
            "title": f"{name}_{uuid.uuid4().hex[:6]}",
            "train_mode": "fast",
            "visibility": "private",
            # 잡음 제거·크기 맞춤. 문서 기본값(true) 그대로지만, 기본값이 바뀌어도 흔들리지 않게 적어 둔다.
            "enhance_audio_quality": "true",
        }
        # texts 는 보내지 않는다(Fish 가 받아쓴다. 청취 비교도 그렇게 만들었다).
        content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
        with audio_path.open("rb") as audio_file:
            response = self._send(
                "POST", "/model", "목소리 만들기", timeout=self.clone_timeout_seconds,
                data=data, files=[("voices", (audio_path.name, audio_file, content_type))],
            )
        if response.status_code not in (200, 201):
            raise _fail(response, "목소리 만들기")
        try:
            model = response.json()
        except ValueError:
            model = {}
        model_id = str(model.get("_id") or "") if isinstance(model, dict) else ""
        if not model_id:
            raise TTSProviderError("Fish Audio 목소리 만들기 응답에 _id 가 없다.")
        voice_id = f"{VOICE_ID_PREFIX}{model_id}"
        state = self._wait_trained(voice_id, model.get("state"))
        if state != "trained":
            self._discard(voice_id)
            if state == "failed":
                raise TTSProviderError("Fish Audio 목소리 만들기가 실패했다(state=failed).")
            raise VoiceProviderError(
                VOICE_PROVIDER_UNAVAILABLE, f"Fish Audio 목소리가 준비되지 않았다(state={state})."
            )
        logger.info("fish voice cloned voice_id=%s", voice_id)
        return voice_id

    def _wait_trained(self, voice_id: str, state: object) -> object:
        """trained·failed 가 될 때까지 잠깐(약 30초) 묻는다. 그래도 아니면 마지막 상태를 돌려준다."""
        tries = 0
        while state not in ("trained", "failed") and tries < _POLL_TRIES:
            tries += 1
            time.sleep(_POLL_SECONDS)
            try:
                response = self._send(
                    "GET", f"/model/{_path_id(voice_id)}", "목소리 상태", timeout=_POLL_TIMEOUT_SECONDS
                )
                if response.status_code == 200:
                    state = (response.json() or {}).get("state")
            except (VoiceProviderError, ValueError, AttributeError):
                continue  # 잠깐 막힌 것이면 다음에 다시 묻는다
        return state

    def _discard(self, voice_id: str) -> None:
        """쓰지 못할 목소리를 지운다(녹음이 Fish 에 남지 않게). 실패해도 기록만 남긴다."""
        try:
            self.delete_voice(voice_id)
        except AppError as error:
            logger.warning("fish voice cleanup failed voice_id=%s code=%s", voice_id, error.code)

    def delete_voice(self, voice_id: str) -> None:
        """Fish 에서 목소리를 지운다(대화 상대를 지울 때·목소리를 다시 만들 때). 이미 없으면 넘어간다."""
        response = self._send(
            "DELETE", f"/model/{_path_id(voice_id)}", "목소리 지우기", timeout=self.timeout_seconds
        )
        if response.status_code == 404:
            logger.info("fish voice already gone voice_id=%s", voice_id)
            return
        if response.status_code not in (200, 204):
            raise _fail(response, "목소리 지우기")
        logger.info("fish voice deleted voice_id=%s", voice_id)
