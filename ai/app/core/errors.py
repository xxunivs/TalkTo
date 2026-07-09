from typing import Any

from fastapi import Request
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


class FeatureNotImplementedError(AppError):
    def __init__(self, feature: str) -> None:
        super().__init__(
            code="feature_not_implemented",
            message=f"{feature} is not implemented yet.",
            status_code=501,
        )


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
