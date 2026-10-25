"""OpenAI 가 거절하면(크레딧·키·요청 한도) 503 과 코드로 답한다. 원문은 돌려주지 않는다."""

import unittest
from unittest.mock import patch

import httpx
import openai
from fastapi.testclient import TestClient

from app.error import (
    PROVIDER_AUTH,
    PROVIDER_BUSY,
    PROVIDER_INPUT_TOO_LARGE,
    PROVIDER_QUOTA,
    PROVIDER_REJECTED,
    provider_error_code,
)
from main import app


def _error(kind, status: int, body: dict):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/x"), json=body)
    return kind("rejected", response=response, body=body)


QUOTA = {"error": {"message": "You have no credits remaining.", "type": "insufficient_quota", "code": "credit_balance_exhausted"}}


class ProviderErrorTest(unittest.TestCase):
    def test_codes(self) -> None:
        self.assertEqual(provider_error_code(_error(openai.RateLimitError, 429, QUOTA)), PROVIDER_QUOTA)
        busy = {"error": {"message": "Rate limit reached", "type": "requests", "code": "rate_limit_exceeded"}}
        self.assertEqual(provider_error_code(_error(openai.RateLimitError, 429, busy)), PROVIDER_BUSY)
        auth = {"error": {"message": "Incorrect API key", "type": "invalid_request_error", "code": "invalid_api_key"}}
        self.assertEqual(provider_error_code(_error(openai.AuthenticationError, 401, auth)), PROVIDER_AUTH)

    def test_reflection_returns_503_with_code_and_no_raw_text(self) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        with patch(
            "app.api.routes.persona.run_reflection",
            side_effect=_error(openai.RateLimitError, 429, QUOTA),
        ):
            response = client.post(
                "/v1/persona/reflection",
                json={"memories": [{"id": "m1", "memoryText": "기억", "importanceScore": 5}]},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], PROVIDER_QUOTA)
        self.assertNotIn("credits", response.text)


_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


class MoreProviderErrorsTest(unittest.TestCase):
    """시간 초과·연결 실패·OpenAI 5xx 도 503 + BUSY(BE 가 회사 문제로 알아본다). 400 은 422 + 코드(다시 해도 같음)."""

    def _post(self, error: Exception):
        client = TestClient(app, raise_server_exceptions=False)
        with patch("app.api.routes.persona.run_reflection", side_effect=error):
            return client.post(
                "/v1/persona/reflection",
                json={"memories": [{"id": "m1", "memoryText": "기억", "importanceScore": 5}]},
            )

    def test_timeouts_connection_errors_and_5xx_are_busy(self) -> None:
        server = {"error": {"message": "The server had an error SECRET-DETAIL", "type": "server_error"}}
        for error in (
            openai.APITimeoutError(request=_REQUEST),
            openai.APIConnectionError(request=_REQUEST),
            _error(openai.InternalServerError, 500, server),
        ):
            response = self._post(error)
            self.assertEqual(response.status_code, 503, type(error).__name__)
            self.assertEqual(response.json()["error"]["code"], PROVIDER_BUSY)
            self.assertNotIn("SECRET-DETAIL", response.text)

    def test_bad_request_is_coded_422(self) -> None:
        too_long = {"error": {"message": "This model's maximum context length is 128000 tokens. 할머니 비밀",
                              "type": "invalid_request_error", "code": "context_length_exceeded"}}
        response = self._post(_error(openai.BadRequestError, 400, too_long))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], PROVIDER_INPUT_TOO_LARGE)
        self.assertNotIn("비밀", response.text)

        other = {"error": {"message": "Invalid value for 'role'", "type": "invalid_request_error", "code": None}}
        response = self._post(_error(openai.BadRequestError, 400, other))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], PROVIDER_REJECTED)


if __name__ == "__main__":
    unittest.main()
