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
        response = self.client.post(
            "/v1/persona/responses",
            json={
                "message": "test",
                "history": [],
                "memories": [],
                "persona": {
                    "subjectId": "subject-test",
                    "instructions": "Generic test instructions.",
                },
            },
        )

        self.assertEqual(response.status_code, 501)
        self.assertEqual(
            response.json()["error"]["code"],
            "feature_not_implemented",
        )


if __name__ == "__main__":
    unittest.main()
