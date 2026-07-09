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


class RecordingAnalysisTest(unittest.TestCase):
    """POST /v1/analysis/recording — 인물·민감·3-A·요약·④ 5콜을 목킹해 통합 매핑을 검증."""

    @classmethod
    def setUpClass(cls) -> None:
        app.dependency_overrides[require_internal_request] = lambda: None
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        app.dependency_overrides.pop(require_internal_request, None)

    @patch("app.providers.llm.OpenAI")
    def test_combined_analysis_maps_all_sections(self, mock_openai: MagicMock) -> None:
        seg_id = str(uuid.uuid4())
        client = MagicMock()
        # 호출 순서: 인물 → 민감 → 3-A 기억 → ④ 언어스타일 → 요약
        client.chat.completions.create.side_effect = [
            _chat_response('{"persons": [], "unresolvedMentions": []}'),
            _chat_response('{"sensitivityFlags": []}'),
            _chat_response(
                '{"memorySegments": [{"memoryText": "지영은 회를 좋아한다.",'
                ' "sourceSegmentIds": [0], "relatedPeople": ["지영"],'
                ' "confidence": "confirmed", "importance": 7, "tags": ["음식요리"]}]}'
            ),
            _chat_response(
                '{"linguisticStyle": {"recurringPhrases": [{"phrase": "그래",'
                ' "sourceSegmentIds": [0]}], "addressTerms": [],'
                ' "sentencePatterns": ["짧은 단문"], "emotionalExpressions": []}}'
            ),
            _chat_response('{"summary": "회를 좋아한다는 이야기를 나눴다."}'),
        ]
        mock_openai.return_value = client

        response = self.client.post(
            "/v1/analysis/recording",
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
        body = response.json()
        # 3-A 기억: 근거 int → 요청 UUID 매핑 + 전체 필드
        self.assertEqual(len(body["memorySegments"]), 1)
        memory = body["memorySegments"][0]
        self.assertEqual(memory["sourceTranscriptSegmentIds"], [seg_id])
        self.assertEqual(memory["importanceScore"], 7)
        self.assertEqual(memory["relatedPeople"], ["지영"])
        # 요약·태그(기억 태그 집계)·말투(④)·안전
        self.assertEqual(body["summary"], "회를 좋아한다는 이야기를 나눴다.")
        self.assertEqual(body["tags"], ["음식요리"])
        self.assertEqual(body["speechStyle"]["recurringPhrases"][0]["phrase"], "그래")
        self.assertEqual(
            body["speechStyle"]["recurringPhrases"][0]["sourceTranscriptSegmentIds"],
            [seg_id],
        )
        self.assertEqual(body["speechStyle"]["sentencePatterns"], ["짧은 단문"])
        self.assertEqual(body["safetyFlags"], [])


if __name__ == "__main__":
    unittest.main()
