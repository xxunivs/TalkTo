"""OpenAI 가 거절하면(크레딧·키·요청 한도) 503 과 코드로 답한다. 원문은 돌려주지 않는다."""

import unittest
from unittest.mock import patch

import httpx
import openai
from fastapi.testclient import TestClient

from infra.error import PROVIDER_AUTH, PROVIDER_BUSY, PROVIDER_QUOTA, provider_error_code
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
            "infra.be_ai_api.routes.persona.run_reflection",
            side_effect=_error(openai.RateLimitError, 429, QUOTA),
        ):
            response = client.post(
                "/v1/persona/reflection",
                json={"memories": [{"id": "m1", "memoryText": "기억", "importanceScore": 5}]},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], PROVIDER_QUOTA)
        self.assertNotIn("credits", response.text)


if __name__ == "__main__":
    unittest.main()
