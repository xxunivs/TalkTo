# AI_SERVER 입장 토큰 확인

import secrets

from fastapi import Header, HTTPException, status

from infra.setting import Settings


def verify_internal_token(
    settings: Settings,
    provided_token: str | None,
) -> None:
    configured_token = settings.ai_server_token
    if configured_token is None:
        # 배포 환경에 토큰이 없으면 막는다(시작 검사를 비켜 간 경우에도 열리지 않게)
        if settings.deployed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "internal_token_not_configured",
                    "message": "The AI server token is not configured.",
                },
            )
        return

    expected = configured_token.get_secret_value()
    # 바이트로 비교한다. 문자열 비교는 ASCII 밖 글자가 오면 TypeError(500)가 난다.
    if not provided_token or not secrets.compare_digest(
        expected.encode(), provided_token.encode()
    ):
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
