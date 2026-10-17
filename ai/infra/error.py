# 에러 종류별 응답 형식

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details




class ProviderNotConfiguredError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            code="provider_not_configured",
            message=detail,
            status_code=500,
        )


class AudioUrlExpiredError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="AUDIO_URL_EXPIRED",
            message="The presigned audio URL was rejected by storage. Reissue the URL and retry.",
            status_code=422,
        )


class AudioDownloadFailedError(AppError):
    def __init__(self, reason: str) -> None:
        super().__init__(
            code="AUDIO_DOWNLOAD_FAILED",
            message=f"Failed to download the recording audio: {reason}",
            status_code=422,
        )


class EmptyTranscriptError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="EMPTY_TRANSCRIPT",
            message="No speech was recognized in the recording.",
            status_code=422,
        )


class AudioTooLargeError(AppError):
    def __init__(self, max_bytes: int) -> None:
        super().__init__(
            code="AUDIO_TOO_LARGE",
            message=f"Audio exceeds the {max_bytes} byte limit.",
            status_code=413,
        )


class AudioTooShortError(AppError):
    def __init__(self, speech_ms: int, minimum_ms: int) -> None:
        super().__init__(
            code="AUDIO_TOO_SHORT",
            message=f"Recognized speech is too short ({speech_ms}ms < {minimum_ms}ms).",
            status_code=422,
        )


class InvalidVoiceSampleError(AppError):
    """클론 샘플 입력 문제(구간이 오디오 밖·너무 짧음·컷 실패). 재시도해도 같은 결과."""

    def __init__(self, message: str) -> None:
        super().__init__(
            code="INVALID_VOICE_SAMPLE",
            message=message,
            status_code=422,
        )


class STTProviderError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(
            code="STT_PROVIDER_ERROR",
            message=message,
            status_code=502,
        )


class TTSProviderError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(
            code="TTS_PROVIDER_ERROR",
            message=message,
            status_code=502,
        )


# 목소리 회사(Fish Audio) 쪽에서 막힌 경우. OpenAI 의 AI_PROVIDER_* 와 따로 둔다 — 백엔드가 목소리용
# 관리자 알림을 띄우고, 대화 전체가 멈춘 것으로 보지 않는다.
VOICE_PROVIDER_KEY = "VOICE_PROVIDER_KEY"  # 키가 틀렸거나 막힘(401·403)
VOICE_PROVIDER_CREDITS = "VOICE_PROVIDER_CREDITS"  # 잔액이 다 됨(402, 충전해야 풀린다)
VOICE_PROVIDER_RATE_LIMIT = "VOICE_PROVIDER_RATE_LIMIT"  # 동시 요청 한도(429, 곧 풀린다)
VOICE_PROVIDER_UNAVAILABLE = "VOICE_PROVIDER_UNAVAILABLE"  # 5xx·시간 초과·연결 실패


class VoiceProviderError(AppError):
    """목소리 회사가 거절·실패한 요청. AI_PROVIDER_* 처럼 503 + 코드로 답한다(회사 원문은 로그에만)."""

    def __init__(self, code: str, message: str = "Voice provider unavailable") -> None:
        super().__init__(code=code, message=message, status_code=503)


class MissingVoiceError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            code="MISSING_VOICE_ID",
            message=detail,
            status_code=400,
        )


async def app_error_handler(_request: Request, error: AppError) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "code": error.code,
            "message": error.message,
        }
    }
    if error.details:
        body["error"]["details"] = error.details
    return JSONResponse(status_code=error.status_code, content=body)


async def validation_error_handler(
    _request: Request, error: RequestValidationError
) -> JSONResponse:
    """요청 형식 오류. 받은 값(input)은 되돌려 주지 않는다(원문·서명된 주소가 담길 수 있다)."""
    detail = [
        {"type": item.get("type"), "loc": list(item.get("loc") or ()), "msg": item.get("msg")}
        for item in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


# AI 회사(OpenAI) 쪽에서 막힌 경우. 백엔드가 알아보고 관리자 화면에 알린다(고객에게는 차분한 문장).
PROVIDER_QUOTA = "AI_PROVIDER_QUOTA"  # 크레딧·사용 한도가 다 됨(충전해야 풀린다)
PROVIDER_AUTH = "AI_PROVIDER_AUTH"  # 키가 틀렸거나 막힘
PROVIDER_BUSY = "AI_PROVIDER_BUSY"  # 잠깐 요청이 몰림(곧 풀린다)


def provider_error_code(error: Exception) -> str:
    """OpenAI 오류를 세 가지로 나눈다(본문의 error.code·type 을 본다)."""
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    detail = detail if isinstance(detail, dict) else {}
    marks = {str(getattr(error, "code", "") or ""), str(detail.get("code") or ""), str(detail.get("type") or "")}
    if marks & {"insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached"}:
        return PROVIDER_QUOTA
    if getattr(error, "status_code", None) in (401, 403):
        return PROVIDER_AUTH
    return PROVIDER_BUSY


async def provider_error_handler(_request: Request, error: Exception) -> JSONResponse:
    """OpenAI 가 거절한 요청(429·401·403). 원문(키·결제 주소)은 돌려주지 않는다."""
    code = provider_error_code(error)
    return JSONResponse(
        status_code=503,
        content={"error": {"code": code, "message": "AI provider unavailable"}},
    )

