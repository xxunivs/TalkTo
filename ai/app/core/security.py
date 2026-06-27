import secrets

from fastapi import Header, HTTPException, status

from app.core.config import Settings


def verify_internal_token(
    settings: Settings,
    provided_token: str | None,
) -> None:
    configured_token = settings.ai_server_token
    if configured_token is None:
        return

    expected = configured_token.get_secret_value()
    if not provided_token or not secrets.compare_digest(expected, provided_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "invalid_internal_token",
                "message": "A valid internal AI server token is required.",
            },
        )


def internal_token_header(
    x_ai_server_token: str | None = Header(default=None),
) -> str | None:
    return x_ai_server_token
