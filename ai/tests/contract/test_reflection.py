import unittest
import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.api.dependencies import require_internal_request
from app.main import app


def _chat_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    return resp


class ReflectionTest(unittest.TestCase):
    """POST /v1/persona/reflection — 통찰의 근거 번호를 실제 기억 id로 매핑하고,
    근거 2개 미만·통제 어휘 밖 카테고리는 버리는지 검증(무API)."""

    @classmethod
    def setUpClass(cls) -> None:
        app.dependency_overrides[require_internal_request] = lambda: None
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        app.dependency_overrides.pop(require_internal_request, None)

    @patch("app.providers.llm.OpenAI")
    def test_maps_evidence_and_drops_unsupported(self, mock_openai: MagicMock) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _chat_response(
            '{"reflections": ['
            ' {"insight": "가족을 중요하게 여긴다.", "category": "가치관",'
            '  "evidence": [1, 2], "importance": 8},'
            ' {"insight": "근거 하나뿐(버려져야).", "category": "성향",'
            '  "evidence": [1], "importance": 5},'
            ' {"insight": "카테고리 이상(버려져야).", "category": "엉뚱",'
            '  "evidence": [1, 2], "importance": 5}'
            ']}'
        )
        mock_openai.return_value = client
        mem_a, mem_b = str(uuid.uuid4()), str(uuid.uuid4())

        response = self.client.post(
            "/v1/persona/reflection",
            json={
                "subjectContext": {"subject": {"name": "김분남"}},
                "memories": [
                    {"id": mem_a, "memoryText": "가족과 밥을 자주 먹었다."},
                    {"id": mem_b, "memoryText": "명절마다 가족이 모였다."},
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        reflections = response.json()["reflections"]
        # 근거<2, 카테고리 이상 둘은 드롭 → 하나만 남는다
        self.assertEqual(len(reflections), 1)
        kept = reflections[0]
        self.assertEqual(kept["category"], "가치관")
        # 근거 번호 1·2가 실제 기억 id로 매핑
        self.assertEqual(sorted(kept["evidenceMemoryIds"]), sorted([mem_a, mem_b]))


if __name__ == "__main__":
    unittest.main()
