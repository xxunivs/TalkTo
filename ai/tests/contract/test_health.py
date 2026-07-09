import unittest

from fastapi.testclient import TestClient

from app.main import app


class HealthEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "service": "talkto-app-ai"},
        )

    def test_ready_does_not_call_providers(self) -> None:
        response = self.client.get("/ready")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["checks"]["openai"]["status"], "not_checked")
        self.assertEqual(body["checks"]["tts"]["status"], "not_checked")

    def test_feature_endpoint_is_an_explicit_placeholder(self) -> None:
        # voice STT는 아직 미구현 스텁 — 요청이 검증을 통과하면 명시적 501을 반환해야 한다.
        response = self.client.post(
            "/v1/voice/transcriptions",
            json={
                "requestId": "00000000-0000-0000-0000-000000000001",
                "language": "ko",
            },
        )

        self.assertEqual(response.status_code, 501)
        self.assertEqual(
            response.json()["error"]["code"],
            "feature_not_implemented",
        )


if __name__ == "__main__":
    unittest.main()
