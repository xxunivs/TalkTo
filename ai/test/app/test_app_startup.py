import logging
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.log import configure_logging
from app.setting import Settings, get_settings


def _app(settings: Settings):
    """설정을 바꿔 앱을 새로 만든다(요청의 설정 의존성도 같은 설정으로)."""
    from main import create_app

    # 배포 설정이면 시작할 때 임시 폴더를 비운다. 테스트가 이 PC 의 진짜 임시 폴더를 비우지 않게 막는다
    # (그 동작은 test_temp_files.py 가 따로 본다)
    with patch("main.get_settings", return_value=settings), patch("main.reset_scratch_dir"):
        app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return app


class StartupGuardTest(unittest.TestCase):
    """배포 환경(staging·production)은 토큰이 없으면 시작하지 않는다(fail closed)."""

    def test_deployed_without_token_refuses_to_start(self) -> None:
        for env in ("staging", "production"):
            with self.assertRaises(RuntimeError, msg=env):
                _app(Settings(app_env=env))

    def test_local_and_test_start_without_token(self) -> None:
        for env in ("local", "test"):
            self.assertEqual(_app(Settings(app_env=env)).title, "TalkTo APP AI")


class DocsExposureTest(unittest.TestCase):
    """배포 환경은 /docs·/redoc·/openapi.json 을 닫고, 로컬은 연다."""

    def test_deployed_hides_docs(self) -> None:
        client = TestClient(_app(Settings(app_env="production", ai_server_token="t")))
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(client.get(path).status_code, 404, path)
        self.assertEqual(client.get("/health").status_code, 200)

    def test_local_keeps_docs(self) -> None:
        client = TestClient(_app(Settings(app_env="local")))
        self.assertEqual(client.get("/openapi.json").status_code, 200)
        self.assertEqual(client.get("/docs").status_code, 200)


class ValidationErrorBodyTest(unittest.TestCase):
    """422 는 type·loc·msg 만 돌려준다. 받은 값(서명된 주소·원문)을 되돌려 주지 않는다."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(_app(Settings(app_env="test")))

    def _assert_no_echo(self, response, secret: str) -> list[dict]:
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret, response.text)
        detail = response.json()["detail"]
        self.assertTrue(detail)
        for item in detail:
            self.assertEqual(set(item), {"type", "loc", "msg"})
        return detail

    def test_missing_field_does_not_echo_body(self) -> None:
        # 빠진 필드 오류의 input 은 요청 본문 전체라, 그대로 두면 서명된 주소가 돌아간다
        secret = "X-Amz-Signature=SECRETSIG"
        response = self.client.post(
            "/v1/analysis/transcriptions",
            json={
                "recordingId": "22222222-2222-2222-2222-222222222222",
                "audioUrl": f"https://storage.example.com/a.m4a?{secret}",
            },
        )
        detail = self._assert_no_echo(response, "SECRETSIG")
        self.assertIn(["body", "jobId"], [item["loc"] for item in detail])
        self.assertIn("missing", [item["type"] for item in detail])

    def test_bad_value_does_not_echo_input(self) -> None:
        response = self.client.post(
            "/v1/voice/speech", json={"text": "할머니 비밀 이야기", "speed": "SECRET-SPEED"}
        )
        self._assert_no_echo(response, "SECRET-SPEED")
        self.assertNotIn("할머니 비밀 이야기", response.text)


class QuietLibraryLoggersTest(unittest.TestCase):
    """httpx(서명된 주소)·openai(요청 본문) 로그는 LOG_LEVEL 과 무관하게 경고부터만."""

    def test_library_loggers_are_warning(self) -> None:
        configure_logging("DEBUG")
        for name in ("httpx", "httpcore", "openai"):
            self.assertEqual(logging.getLogger(name).level, logging.WARNING, name)
        self.assertFalse(logging.getLogger("httpx").isEnabledFor(logging.INFO))


if __name__ == "__main__":
    unittest.main()
