import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


class EmbeddingsTest(unittest.TestCase):
    """POST /v1/embeddings — OpenAI 호출을 목킹해 항목→벡터 매핑을 검증(무API)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @patch("app.providers.llm.OpenAI")
    def test_maps_each_item_to_a_vector(self, mock_openai: MagicMock) -> None:
        resp = MagicMock()
        resp.data = [MagicMock(embedding=[0.1, 0.2]), MagicMock(embedding=[0.3, 0.4])]
        client = MagicMock()
        client.embeddings.create.return_value = resp
        mock_openai.return_value = client

        response = self.client.post(
            "/v1/embeddings",
            json={
                "jobId": "00000000-0000-0000-0000-000000000001",
                "items": [
                    {"memorySegmentId": "00000000-0000-0000-0000-000000000002",
                     "embeddingIndex": 0, "text": "첫 기억"},
                    {"memorySegmentId": "00000000-0000-0000-0000-000000000003",
                     "embeddingIndex": 1, "text": "둘째 기억"},
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        out = response.json()["embeddings"]
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["memorySegmentId"], "00000000-0000-0000-0000-000000000002")
        self.assertEqual(out[0]["embedding"], [0.1, 0.2])
        self.assertEqual(out[1]["embedding"], [0.3, 0.4])


if __name__ == "__main__":
    unittest.main()
