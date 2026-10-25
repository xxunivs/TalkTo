"""Fish Audio 목소리 제공사: 즉시 복제, drama-3 합성, 삭제 (voice_id = "fish_" + 모델 _id)"""

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
# 합성 모델 고정 (헤더 누락, 오타 시 경고 없이 s2.1-pro 로 합성, 테스트가 값 확인)
MODEL = "drama-3"
VOICE_ID_PREFIX = "fish_"
# 합성 1회 전체 마감 (재시도 포함, BE 45초 한도보다 짧게)
# httpx timeout 은 단계별이라 전체를 묶지 못해 _speak 에서 조각마다 직접 확인
_TIMEOUT_SECONDS = 40.0
_CONNECT_SECONDS = 5.0
_CLONE_TIMEOUT_SECONDS = 120.0  # 1분 안팎 wav (약 5MB) 업로드
# 합성만 1회 재시도 (목소리 생성 재전송 시 목소리 중복 생성)
_RETRY_STATUS = frozenset({429, 503})
_RETRY_PAUSE_SECONDS = 1.0
_RETRY_MIN_LEFT_SECONDS = 10.0  # 남은 시간이 이보다 적으면 재시도 생략
_POLL_TRIES = 15
_POLL_SECONDS = 2.0
_POLL_TIMEOUT_SECONDS = 10.0  # 상태 조회 1회 한도 (전체 대기 시간 보호)
_CODES = {
    401: VOICE_PROVIDER_KEY,
    403: VOICE_PROVIDER_KEY,
    402: VOICE_PROVIDER_CREDITS,
    429: VOICE_PROVIDER_RATE_LIMIT,
}
_MODEL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")

# drama-3 가 지시로 읽는 [ ], < >, <|speaker|> 표시 제거
# 예외: 아래 두 목록의 한국어 연기 표시 (FISH_EMOTION=cue, inline 일 때만)
_SPEAKER_TAG = re.compile(r"<\|[^|<>\n]*\|>")
_ANGLE_TAG = re.compile(r"<[^<>\n]{0,40}>")
_SQUARE = re.compile(r"\[[^\[\]\n]*\]")

# FISH_EMOTION=cue: 감정 이름 -> 대답 맨 앞 한국어 연기 표시 (neutral 은 drama-3 자체 판단)
# 대상자 페르소나는 sad 가 calm 으로 변환 (pipeline/s4_voice/b_emotion.py), 우는 소리 표시 없음
FISH_CUES: dict[str, str | None] = {
    "neutral": None,
    "calm": "[차분하게]",
    "warm": "[다정하게]",
    "happy": "[반갑게 웃으며]",
    "sad": "[조용히 서운하게]",
    "surprised": "[깜짝 놀라며]",
    "worried": "[걱정스럽게]",
}
# FISH_EMOTION=inline: 대답 안 허용 연기 표시 (그 외 제거, 울음, 흐느낌은 안전선으로 제외)
FISH_INLINE_CUES: tuple[str, ...] = (
    "[웃으며]",
    "[작게 웃으며]",
    "[한숨 쉬며]",
    "[다정하게]",
    "[반갑게]",
    "[놀라며]",
    "[걱정스럽게]",
    "[차분하게]",
    "[작게]",
    "[잠깐 멈췄다가]",
)
# fish_plain_text 가 남기는 표시 (두 방식 합집합)
_KEPT_CUES = frozenset(FISH_INLINE_CUES) | {cue for cue in FISH_CUES.values() if cue}


def cue_key(raw: str) -> str:
    """연기 표시 대괄호 안 띄어쓰기 정규화"""
    return "[" + " ".join(raw[1:-1].split()) + "]"


def is_fish_voice(voice_id: str) -> bool:
    return voice_id.startswith(VOICE_ID_PREFIX)


def fish_model_id(voice_id: str) -> str:
    """voice_id -> Fish 모델 _id (접두어 제거)"""
    return voice_id.removeprefix(VOICE_ID_PREFIX)


def strip_fish_cues(text: str) -> str:
    """Fish 연기 표시 제거 (MiniMax, ElevenLabs 는 표시를 소리 내어 읽음)"""
    found = False

    def square(m: re.Match) -> str:
        nonlocal found
        if cue_key(m.group(0)) in _KEPT_CUES:
            found = True
            return " "
        return m.group(0)

    clean = _SQUARE.sub(square, text)
    return _tidy(clean) if found else text  # 표시가 없으면 원문 그대로


def fish_plain_text(text: str, *, keep_cues: bool = True) -> str:
    """drama-3 전송용 글 (지시 표시 제거, keep_cues 면 정해 둔 연기 표시 유지, 전부 지워지면 원문)"""

    def square(m: re.Match) -> str:
        key = cue_key(m.group(0))
        return key if keep_cues and key in _KEPT_CUES else " "

    clean = _SQUARE.sub(square, _ANGLE_TAG.sub(" ", _SPEAKER_TAG.sub(" ", text)))
    return _tidy(clean) or text


def _tidy(clean: str) -> str:
    """표시 제거 자리 띄어쓰기 정리"""
    clean = re.sub(r"[ \t]+([.,!?…])", r"\1", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    return "\n".join(line.strip() for line in clean.splitlines()).strip()


def _fail(response: httpx.Response, what: str) -> AppError:
    """Fish 오류 응답 -> 코드 있는 503 (키, 잔액, 한도, 장애) 또는 502"""
    status = response.status_code
    code = _CODES.get(status) or (VOICE_PROVIDER_UNAVAILABLE if status >= 500 else None)
    if code:
        logger.warning(
            "fish %s rejected status=%s code=%s body=%s", what, status, code, response.text[:200]
        )
        return VoiceProviderError(code, f"Fish Audio {what} 오류 {status}")
    return TTSProviderError(f"Fish Audio {what} 오류 {status}: {response.text[:200]}")


def _unavailable(what: str, error: httpx.HTTPError) -> VoiceProviderError:
    """시간 초과, 연결 실패 -> VOICE_PROVIDER_UNAVAILABLE"""
    return VoiceProviderError(
        VOICE_PROVIDER_UNAVAILABLE, f"Fish Audio {what} 요청 실패({type(error).__name__})"
    )


def _path_id(voice_id: str) -> str:
    """Fish 전송용 모델 id 검증 (빈 reference_id 합성 시 엉뚱한 목소리)"""
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
        emotion_mode: str = "off",
        happy_temperature: float | None = None,
        timeout_seconds: float = _TIMEOUT_SECONDS,
        clone_timeout_seconds: float = _CLONE_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key
        # FISH_EMOTION (off 면 표시 전부 제거, 저장된 표시도 즉시 무시)
        self.emotion_mode = emotion_mode
        # 기쁜 대답 전용 temperature (FISH_HAPPY_TEMPERATURE, None 또는 0 이하면 Fish 기본값)
        self.happy_temperature = (
            happy_temperature if happy_temperature and happy_temperature > 0 else None
        )
        self.timeout_seconds = timeout_seconds
        self.clone_timeout_seconds = clone_timeout_seconds

    def _send(
        self,
        method: str,
        path: str,
        what: str,
        *,
        timeout: float,
        headers: dict[str, str] | None = None,
        **kwargs,
    ) -> httpx.Response:
        try:
            return httpx.request(
                method,
                f"{FISH_BASE}{path}",
                headers={"Authorization": f"Bearer {self.api_key}", **(headers or {})},
                timeout=timeout,
                **kwargs,
            )
        except httpx.HTTPError as error:  # 시간 초과, 연결 실패
            raise _unavailable(what, error) from error

    def _speak(self, body: dict, deadline: float) -> tuple[httpx.Response, bytes]:
        """합성 요청 1회 (조각 수신 중 deadline 초과 시 중단, 조각 사이 정지는 읽기 제한까지 대기)"""
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
                    response.read()  # 짧은 오류 JSON (로그, 코드용)
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
        # speed, stability, similarity_boost, style 은 ElevenLabs 전용이라 미사용
        # 감정은 연기 표시로만 전달 (cue: 맨 앞 FISH_CUES, cue 와 inline: 글 안 정해진 표시 유지)
        spoken = fish_plain_text(text, keep_cues=self.emotion_mode != "off")
        cue = FISH_CUES.get(emotion or "") if self.emotion_mode == "cue" else None
        if cue:
            spoken = f"{cue} {spoken}"
        body = {
            "text": spoken,
            "reference_id": _path_id(voice_id),
            "format": "mp3",
            "sample_rate": 44100,
            "mp3_bitrate": 128,
            "latency": "normal",
        }
        # [반갑게 웃으며] 의 높은 음 완화용 temperature (표시는 유지)
        if cue and emotion == "happy" and self.happy_temperature is not None:
            body["temperature"] = self.happy_temperature
        deadline = time.monotonic() + self.timeout_seconds  # 재시도 포함 마감
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
        """wav 녹음 -> 목소리 생성 -> voice_id (제목 중복 방지 꼬리, name 로그 금지, 중복 생성 방지로 재전송 없음)"""
        data = {
            "type": "tts",
            "title": f"{name}_{uuid.uuid4().hex[:6]}",
            "train_mode": "fast",
            "visibility": "private",
            # 잡음 제거, 크기 맞춤 (문서 기본값 true 지만 기본값 변경 대비 명시)
            "enhance_audio_quality": "true",
        }
        # texts 미전송 (Fish 자체 받아쓰기, 청취 비교와 동일 조건)
        content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
        with audio_path.open("rb") as audio_file:
            response = self._send(
                "POST",
                "/model",
                "목소리 만들기",
                timeout=self.clone_timeout_seconds,
                data=data,
                files=[("voices", (audio_path.name, audio_file, content_type))],
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
        """trained, failed 까지 상태 조회 (약 30초, 이후 마지막 상태 반환)"""
        tries = 0
        while state not in ("trained", "failed") and tries < _POLL_TRIES:
            tries += 1
            time.sleep(_POLL_SECONDS)
            try:
                response = self._send(
                    "GET",
                    f"/model/{_path_id(voice_id)}",
                    "목소리 상태",
                    timeout=_POLL_TIMEOUT_SECONDS,
                )
                if response.status_code == 200:
                    state = (response.json() or {}).get("state")
            except (VoiceProviderError, ValueError, AttributeError):
                continue  # 일시 장애면 다음 조회
        return state

    def _discard(self, voice_id: str) -> None:
        """실패한 목소리 삭제 (녹음이 Fish 에 남지 않게, 실패 시 로그만)"""
        try:
            self.delete_voice(voice_id)
        except AppError as error:
            logger.warning("fish voice cleanup failed voice_id=%s code=%s", voice_id, error.code)

    def delete_voice(self, voice_id: str) -> None:
        """Fish 목소리 삭제 (404 는 통과)"""
        response = self._send(
            "DELETE", f"/model/{_path_id(voice_id)}", "목소리 지우기", timeout=self.timeout_seconds
        )
        if response.status_code == 404:
            logger.info("fish voice already gone voice_id=%s", voice_id)
            return
        if response.status_code not in (200, 204):
            raise _fail(response, "목소리 지우기")
        logger.info("fish voice deleted voice_id=%s", voice_id)
