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
    """말로 답하기(/v1/voice/answer-transcriptions) 녹음에서 말소리를 찾지 못했다.

    BE 는 이 코드를 보고 '다시 한번 말씀해 주세요'(422 no_speech)로 알린다. 다시 해도 같은 녹음이라 422.
    """

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


class EmptyReplyError(AppError):
    """대답 글이 비었다(내용 걸러짐·빈 답). 빈 대답을 BE 가 저장하면 그 대화의 다음 턴이 모두 422 로
    막혔다(대화 기록의 빈 줄). 502 로 답해 BE 가 이번 턴을 되돌리게 한다."""

    def __init__(self) -> None:
        super().__init__(
            code="EMPTY_REPLY", message="The persona reply was empty.", status_code=502
        )


class AnalysisTooLongError(AppError):
    """분석 답이 시간 안에 끝나지 않았거나 길이 한도에서 잘렸다. 같은 녹음은 다시 해도 같다(422)."""

    def __init__(self, stage: str) -> None:
        super().__init__(
            code="ANALYSIS_TOO_LONG",
            message=f"The {stage} step could not finish for this input (too long).",
            status_code=422,
        )


class AnalysisBadOutputError(AppError):
    """분석 답이 JSON 이 아니었다(모델이 형식을 어김). 다시 하면 나을 수 있다(502)."""

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
    """OpenAI 오류를 세 가지로 나눈다(본문의 error.code·type 을 본다).

    시간 초과·연결 실패·OpenAI 5xx 는 본문이 없거나 위 코드가 아니어서 BUSY(곧 풀린다)가 된다.
    """
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
    """OpenAI 가 거절했거나 답하지 않은 요청(429·401·403·5xx·시간 초과·연결 실패).
    원문(키·결제 주소)은 돌려주지 않는다."""
    code = provider_error_code(error)
    logger.warning("openai unavailable: %s -> %s", type(error).__name__, code)
    return JSONResponse(
        status_code=503,
        content={"error": {"code": code, "message": "AI provider unavailable"}},
    )


# OpenAI 가 요청 자체를 받지 않은 경우(400). 회사가 막힌 것이 아니라 다시 보내도 같다 — BE 가 다시 하지 않게 422.
PROVIDER_INPUT_TOO_LARGE = "AI_INPUT_TOO_LARGE"  # 글이 모델 한도(문맥·임베딩 입력)보다 길다
PROVIDER_REJECTED = "AI_PROVIDER_REJECTED"  # 그 밖의 거절
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
    """OpenAI 400. 원문은 돌려주지도 남기지도 않는다(요청 글 일부가 들어 있을 수 있다)."""
    code = bad_request_code(error)
    logger.warning("openai rejected the request: %s -> %s", type(error).__name__, code)
    return JSONResponse(
        status_code=422,
        content={"error": {"code": code, "message": "AI provider rejected the request"}},
    )


async def internal_validation_error_handler(request: Request, error: Exception) -> JSONResponse:
    """요청 형식이 아니라 처리 중에 만든 값이 스키마를 어긴 경우(pydantic ValidationError).

    그대로 두면 500 과 함께 traceback 이 로그에 남고, pydantic 오류 글에는 받은 값(input) 앞뒤가 들어
    가족 대화 글이 로그로 샜다. 같은 입력이면 다시 해도 같으므로 422 + 코드로 답하고, 로그에는 오류
    종류와 위치만 남긴다.
    """
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
