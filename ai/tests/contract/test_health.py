import unittest

from fastapi.testclient import TestClient

from main import app


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

if __name__ == "__main__":
    unittest.main()
