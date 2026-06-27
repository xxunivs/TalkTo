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
