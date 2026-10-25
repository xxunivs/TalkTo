# 에러 종류별 응답 형식

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


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


class NoSpeechError(AppError):
    """말로 답하기 녹음 무음 (BE 가 재요청 안내, 같은 녹음이라 422)"""

    def __init__(self) -> None:
        super().__init__(
            code="NO_SPEECH",
            message="No speech was recognized in the answer recording.",
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
    """클론 샘플 입력 오류 (구간이 오디오 밖, 너무 짧음, 컷 실패, 재시도 무의미)"""

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


# 목소리 제공사(Fish Audio) 장애 코드 (AI_PROVIDER_* 와 분리: BE 가 목소리용 관리자 알림, 대화 중단으로 보지 않음)
VOICE_PROVIDER_KEY = "VOICE_PROVIDER_KEY"  # 키 오류, 차단 (401, 403)
VOICE_PROVIDER_CREDITS = "VOICE_PROVIDER_CREDITS"  # 잔액 소진 (402, 충전 필요)
VOICE_PROVIDER_RATE_LIMIT = "VOICE_PROVIDER_RATE_LIMIT"  # 동시 요청 한도 (429, 일시적)
VOICE_PROVIDER_UNAVAILABLE = "VOICE_PROVIDER_UNAVAILABLE"  # 5xx, 시간 초과, 연결 실패


class VoiceProviderError(AppError):
    """목소리 제공사 거절, 실패 -> 503 + 코드 (제공사 원문은 로그에만)"""

    def __init__(self, code: str, message: str = "Voice provider unavailable") -> None:
        super().__init__(code=code, message=message, status_code=503)


class MissingVoiceError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            code="MISSING_VOICE_ID",
            message=detail,
            status_code=400,
        )


class EmptyReplyError(AppError):
    """빈 대답 -> 502 (BE 가 저장하면 이후 턴이 모두 422 로 막혀서 턴 되돌리기 유도)"""

    def __init__(self) -> None:
        super().__init__(
            code="EMPTY_REPLY", message="The persona reply was empty.", status_code=502
        )


class AnalysisTooLongError(AppError):
    """분석 시간 초과, 길이 한도 잘림 (같은 녹음은 재시도해도 동일, 422)"""

    def __init__(self, stage: str) -> None:
        super().__init__(
            code="ANALYSIS_TOO_LONG",
            message=f"The {stage} step could not finish for this input (too long).",
            status_code=422,
        )


class AnalysisBadOutputError(AppError):
    """분석 답 JSON 형식 위반 (재시도 여지, 502)"""

    def __init__(self, stage: str) -> None:
        super().__init__(
            code="ANALYSIS_BAD_OUTPUT",
            message=f"The {stage} step returned malformed output.",
            status_code=502,
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
    """요청 형식 오류 응답 (input 값 제외: 원문, 서명된 주소 포함 가능)"""
    detail = [
        {"type": item.get("type"), "loc": list(item.get("loc") or ()), "msg": item.get("msg")}
        for item in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


# OpenAI 장애 코드 (BE 가 관리자 화면에 알림, 고객에게는 차분한 문장)
PROVIDER_QUOTA = "AI_PROVIDER_QUOTA"  # 크레딧, 사용 한도 소진 (충전 필요)
PROVIDER_AUTH = "AI_PROVIDER_AUTH"  # 키 오류, 차단
PROVIDER_BUSY = "AI_PROVIDER_BUSY"  # 일시적 요청 몰림


def provider_error_code(error: Exception) -> str:
    """OpenAI 오류 -> QUOTA, AUTH, BUSY (시간 초과, 연결 실패, 5xx 는 BUSY)"""
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    detail = detail if isinstance(detail, dict) else {}
    marks = {
        str(getattr(error, "code", "") or ""),
        str(detail.get("code") or ""),
        str(detail.get("type") or ""),
    }
    if marks & {"insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached"}:
        return PROVIDER_QUOTA
    if getattr(error, "status_code", None) in (401, 403):
        return PROVIDER_AUTH
    return PROVIDER_BUSY


async def provider_error_handler(_request: Request, error: Exception) -> JSONResponse:
    """OpenAI 거절, 무응답 -> 503 + 코드 (원문의 키, 결제 주소 미반환)"""
    code = provider_error_code(error)
    logger.warning("openai unavailable: %s -> %s", type(error).__name__, code)
    return JSONResponse(
        status_code=503,
        content={"error": {"code": code, "message": "AI provider unavailable"}},
    )


# OpenAI 400: 재전송해도 같은 결과라 BE 재시도 방지용 422
PROVIDER_INPUT_TOO_LARGE = "AI_INPUT_TOO_LARGE"  # 모델 한도 초과 (문맥, 임베딩 입력)
PROVIDER_REJECTED = "AI_PROVIDER_REJECTED"  # 기타 거절
_TOO_LARGE_CODES = {"context_length_exceeded", "string_above_max_length", "max_tokens_exceeded"}
_TOO_LARGE_WORDS = ("context length", "maximum", "too long", "too large", "too many", "max ")


def bad_request_code(error: Exception) -> str:
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    detail = detail if isinstance(detail, dict) else {}
    code = str(getattr(error, "code", "") or detail.get("code") or "")
    message = str(detail.get("message") or getattr(error, "message", "") or "").lower()
    if code in _TOO_LARGE_CODES or any(word in message for word in _TOO_LARGE_WORDS):
        return PROVIDER_INPUT_TOO_LARGE
    return PROVIDER_REJECTED


async def provider_bad_request_handler(_request: Request, error: Exception) -> JSONResponse:
    """OpenAI 400 -> 422 (원문은 요청 글 포함 가능성으로 미반환, 미기록)"""
    code = bad_request_code(error)
    logger.warning("openai rejected the request: %s -> %s", type(error).__name__, code)
    return JSONResponse(
        status_code=422,
        content={"error": {"code": code, "message": "AI provider rejected the request"}},
    )


async def internal_validation_error_handler(request: Request, error: Exception) -> JSONResponse:
    """처리 중 생성 값의 스키마 위반 -> 422 (로그에는 종류, 위치만: 기본 오류 글에 가족 대화 원문 유출 이력)"""
    errors = error.errors() if hasattr(error, "errors") else []
    where = [{"type": item.get("type"), "loc": list(item.get("loc") or ())} for item in errors[:10]]
    logger.error(
        "internal data failed validation on %s: %s %s",
        request.url.path,
        getattr(error, "title", type(error).__name__),
        where,
    )
    return JSONResponse(
        status_code=422,
        content={
            "error": {"code": "AI_DATA_INVALID", "message": "AI server produced invalid data"}
        },
    )
