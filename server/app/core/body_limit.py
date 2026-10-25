"""요청 본문 크기 상한(pure ASGI 미들웨어).

Content-Length 가 상한을 넘으면 읽기 전에 413 을 준다. 길이를 밝히지 않고 나눠 보내는
본문(chunked)은 받은 만큼 세다가 넘는 순간 멈추고 413 을 준다. 상한은 경로마다 다르고, 각 화면이
받는 크기보다 조금 넉넉하다(화면이 받는 크기를 바꾸면 여기도 본다 — tests/test_hardening.py).
"""

import jwt
from fastapi import HTTPException, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.security import decode_token

MiB = 1024 * 1024
GiB = 1024 * MiB
TOO_LARGE = "파일이 너무 커요."
TOO_LARGE_BY_KIND = {
    "materials": "파일이 너무 커요. 한 번에 100MB까지 올릴 수 있어요. 나눠서 올려 주세요.",
    "voice": "녹음이 너무 길어요. 조금 짧게 나눠서 보내 주세요.",
    "recordings": "한 번에 올리기엔 너무 커요. 몇 개씩 나눠서 올려 주세요.",
    "transcriptions": "녹음이 너무 길어요. 3분 안으로 나눠서 말씀해 주세요.",
    "consent_audio": "녹음이 너무 길어요. 15분 안으로 녹음해 주세요.",
}
# 파일을 받는 경로(녹음·남긴 글·목소리 메시지)의 큰 본문(8MB 넘게 밝혔거나 길이를 밝히지 않은
# 것)은 서버 전체에서 동시에 MAX_BIG_UPLOADS 개, 한 사람이 MAX_BIG_UPLOADS_PER_USER 개, 합쳐
# MAX_BIG_BYTES 까지만 받는다(디스크·메모리가 한꺼번에 차지 않게). 길이를 밝히지 않은 본문은 그
# 경로 상한만큼으로 센다. 예전에는 100MB 넘게 밝힌 것만 세어, 99MB 를 여럿 보내거나 길이를
# 밝히지 않으면 얼마든지 동시에 받았다.
BIG_BODY = 8 * MiB
MAX_BIG_UPLOADS = 6
MAX_BIG_UPLOADS_PER_USER = 2
MAX_BIG_BYTES = 8 * GiB
BUSY = "지금 다른 큰 파일을 받고 있어요. 잠시 뒤 다시 올려 주세요."
LOGIN_EXPIRED = "로그인이 풀렸어요. 다시 로그인해 주세요."
DEFAULT_LIMIT = 2 * MiB
# 녹음은 한 번에 persona_setup.MAX_RECORDINGS 개 × MAX_UPLOAD_MB 까지 받는다
RECORDING_FILES = 20
RECORDINGS_MIN_LIMIT = 2 * GiB
# 남긴 글: 파일 합 100MB(materials.MAX_REQUEST_MB) + 붙여넣은 글 20만 자
MATERIALS_LIMIT = 110 * MiB
# 목소리 메시지: 10MB(conversations.VOICE_MAX_BYTES)
VOICE_MESSAGE_LIMIT = 11 * MiB
# 말로 답하기: 6MB(transcriptions.MAX_BYTES). 넘으면 라우트가 400, 이보다 크면 여기서 413
TRANSCRIPTION_LIMIT = 7 * MiB
# 그분 동의 녹음: 32MB(subject_consent_max_mb) + multipart 머리글·단계 기록. 넘으면 라우트가 413
CONSENT_AUDIO_LIMIT = 40 * MiB
SLACK = 16 * MiB  # multipart 머리글 몫


def _consent_audio(path: str) -> bool:
    """/personas/{id}/subject-consent/{rid}/audio(그분 동의 녹음 올리기)."""
    return "/subject-consent/" in path and path.endswith("/audio")


def kind_of(path: str) -> str | None:
    path = path.rstrip("/")
    if _consent_audio(path):
        return "consent_audio"
    if path.endswith("/recordings"):
        return "recordings"
    if path.endswith("/materials"):
        return "materials"
    if path.endswith("/messages/voice"):
        return "voice"
    if path.endswith("/transcriptions"):
        return "transcriptions"
    return None


def too_large_message(path: str) -> str:
    return TOO_LARGE_BY_KIND.get(kind_of(path) or "", TOO_LARGE)


def limit_for(path: str) -> int:
    path = path.rstrip("/")
    if _consent_audio(path):
        return CONSENT_AUDIO_LIMIT
    if path.endswith("/recordings"):
        per_file = get_settings().max_upload_mb * MiB
        return max(RECORDINGS_MIN_LIMIT, RECORDING_FILES * per_file + SLACK)
    if path.endswith("/materials"):
        return MATERIALS_LIMIT
    if path.endswith("/messages/voice"):
        return VOICE_MESSAGE_LIMIT
    if path.endswith("/transcriptions"):
        return TRANSCRIPTION_LIMIT
    return DEFAULT_LIMIT


class BodyTooLarge(HTTPException):
    """본문을 읽다가 상한을 넘었다. FastAPI 가 그대로 413 으로 바꾼다."""

    def __init__(self, message: str = TOO_LARGE) -> None:
        super().__init__(status.HTTP_413_CONTENT_TOO_LARGE, message)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _token_subject(scope: Scope) -> str | None:
    """서명·만료가 맞는 access 토큰의 사용자 id(DB 는 보지 않는다. 나머지는 라우트가 본다)."""
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() != "bearer" or not token.strip():
                return None
            try:
                payload = decode_token(token.strip(), "access")
            except jwt.PyJWTError:
                return None
            return str(payload.get("sub") or "")
    return None


async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse({"detail": too_large_message(scope["path"])}, status_code=413)
    await response(scope, receive, send)


async def _unauthorized(scope: Scope, receive: Receive, send: Send) -> None:
    # 401 이면 앱이 토큰을 새로 받아 다시 보낸다(만료된 토큰으로 큰 파일을 올리던 경우)
    response = JSONResponse({"detail": LOGIN_EXPIRED}, status_code=401)
    await response(scope, receive, send)


class BodySizeLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # 지금 받고 있는 큰 업로드 수·밝힌 바이트 합·사람마다 수(이벤트 루프 하나라 잠금이
        # 필요 없다)
        self.big_uploads = 0
        self.big_bytes = 0
        self.big_by_user: dict[str, int] = {}

    def _busy(self, user: str, weight: int) -> bool:
        return (
            self.big_uploads >= MAX_BIG_UPLOADS
            or self.big_bytes + weight > MAX_BIG_BYTES
            or self.big_by_user.get(user, 0) >= MAX_BIG_UPLOADS_PER_USER
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = limit_for(scope["path"])
        declared = _content_length(scope)
        user = _token_subject(scope) if limit > DEFAULT_LIMIT else None
        # 큰 파일은 로그인한 사람만. FastAPI 는 로그인 확인 전에 본문을 /tmp 에 다 받아 두므로,
        # 여기서 막지 않으면 모르는 사람이 GB 단위로 디스크를 채울 수 있다.
        if limit > DEFAULT_LIMIT and user is None:
            if declared is not None and declared > DEFAULT_LIMIT:
                await _unauthorized(scope, receive, send)
                return
            limit = DEFAULT_LIMIT
        if declared is not None and declared > limit:
            await _reject(scope, receive, send)
            return
        big = (
            user is not None
            and limit > DEFAULT_LIMIT
            and scope.get("method", "POST") == "POST"
            and (declared is None or declared > BIG_BODY)
        )
        weight = declared if declared is not None else limit
        if big and self._busy(user, weight):
            await JSONResponse({"detail": BUSY}, status_code=429)(scope, receive, send)
            return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge(too_large_message(scope["path"]))
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        if big:
            self.big_uploads += 1
            self.big_bytes += weight
            self.big_by_user[user] = self.big_by_user.get(user, 0) + 1
        try:
            await self.app(scope, limited_receive, tracked_send)
        except BodyTooLarge:
            # 보통은 FastAPI 가 413 으로 바꾼다. 본문을 다른 곳에서 읽다 새어 나오면 여기서 준다.
            if started:
                raise
            await _reject(scope, receive, send)
        finally:
            if big:
                self.big_uploads -= 1
                self.big_bytes -= weight
                left = self.big_by_user.get(user, 1) - 1
                if left > 0:
                    self.big_by_user[user] = left
                else:
                    self.big_by_user.pop(user, None)
