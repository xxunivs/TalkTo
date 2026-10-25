import unittest
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.dependencies import require_internal_request
from app.authentication import verify_internal_token
from app.setting import Settings, get_settings


class VerifyInternalTokenTest(unittest.TestCase):
    """입장 토큰: 바이트로 비교(ASCII 밖 글자도 401), 배포 환경에 토큰이 없으면 503."""

    def _status(self, settings: Settings, provided: str | None) -> int | None:
        try:
            verify_internal_token(settings, provided)
        except HTTPException as error:
            return error.status_code
        return None

    def test_correct_token_passes(self) -> None:
        self.assertIsNone(self._status(Settings(ai_server_token="s3cret"), "s3cret"))

    def test_wrong_or_missing_token_is_401(self) -> None:
        settings = Settings(ai_server_token="s3cret")
        self.assertEqual(self._status(settings, "wrong"), 401)
        self.assertEqual(self._status(settings, None), 401)
        self.assertEqual(self._status(settings, ""), 401)

    def test_non_ascii_token_is_401_not_type_error(self) -> None:
        settings = Settings(ai_server_token="s3cret")
        self.assertEqual(self._status(settings, "토큰"), 401)
        # Starlette 는 헤더를 latin-1 로 풀어 준다(0x80~0xFF 글자)
        self.assertEqual(self._status(settings, "s3cr\xe9t"), 401)

    def test_missing_configured_token_fails_closed_when_deployed(self) -> None:
        for env in ("staging", "production"):
            self.assertEqual(self._status(Settings(app_env=env), None), 503, env)
            self.assertEqual(self._status(Settings(app_env=env), "anything"), 503, env)

    def test_missing_configured_token_stays_open_locally(self) -> None:
        for env in ("local", "test"):
            self.assertIsNone(self._status(Settings(app_env=env), None), env)


class InternalTokenHeaderTest(unittest.TestCase):
    """HTTP 로 보낸 ASCII 밖 토큰 헤더도 500 이 아니라 401 이다(main.app 의 인증 대역과 무관한 작은 앱)."""

    def setUp(self) -> None:
        app = FastAPI()

        @app.get("/guarded")
        def guarded(_auth: Annotated[None, Depends(require_internal_request)]) -> dict:
            return {"ok": True}

        app.dependency_overrides[get_settings] = lambda: Settings(ai_server_token="s3cret")
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_non_ascii_header_is_401(self) -> None:
        response = self.client.get(
            "/guarded", headers={"X-AI-Server-Token": "토큰".encode("utf-8")}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "invalid_internal_token")

    def test_valid_header_passes(self) -> None:
        response = self.client.get("/guarded", headers={"X-AI-Server-Token": "s3cret"})
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
