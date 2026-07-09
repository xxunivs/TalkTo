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


class MemorySegmentsTest(unittest.TestCase):
    """POST /v1/analysis/memory-segments — 인물·민감·3-A 3콜을 목킹해 매핑을 검증(무API)."""

    @classmethod
    def setUpClass(cls) -> None:
        app.dependency_overrides[require_internal_request] = lambda: None
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        app.dependency_overrides.pop(require_internal_request, None)

    @patch("app.providers.llm.OpenAI")
    def test_maps_pipeline_output_to_contract(self, mock_openai: MagicMock) -> None:
        seg_id = str(uuid.uuid4())
        client = MagicMock()
        # 호출 순서: 인물 → 민감 → 3-A 기억
        client.chat.completions.create.side_effect = [
            _chat_response('{"persons": [], "unresolvedMentions": []}'),
            _chat_response('{"sensitivityFlags": []}'),
            _chat_response(
                '{"memorySegments": [{"memoryText": "지영은 회를 좋아한다.",'
                ' "sourceSegmentIds": [0], "relatedPeople": ["지영"],'
                ' "confidence": "confirmed", "importance": 7, "tags": ["음식요리"]}]}'
            ),
        ]
        mock_openai.return_value = client

        response = self.client.post(
            "/v1/analysis/memory-segments",
            json={
                "jobId": str(uuid.uuid4()),
                "recordingId": str(uuid.uuid4()),
                "transcriptSegments": [
                    {"id": seg_id, "segmentIndex": 0, "startMs": 0, "endMs": 1000,
                     "speakerLabel": "SPK_1", "transcriptText": "회 좋아하지."},
                ],
                "subjectContext": {"subject": {"name": "김분남", "addressTerm": "할머니"}},
                "conversationPartnerName": "지영",
            },
        )

        self.assertEqual(response.status_code, 200)
        out = response.json()["segments"]
        self.assertEqual(len(out), 1)
        memory = out[0]
        # 근거 int 인덱스가 요청의 UUID로 매핑된다
        self.assertEqual(memory["sourceTranscriptSegmentIds"], [seg_id])
        self.assertEqual(memory["confidence"], "confirmed")
        self.assertEqual(memory["importanceScore"], 7)
        self.assertEqual(memory["tags"], ["음식요리"])
        self.assertEqual(memory["relatedPeople"], ["지영"])


if __name__ == "__main__":
    unittest.main()
