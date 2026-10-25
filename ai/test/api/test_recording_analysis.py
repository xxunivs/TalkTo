import unittest
import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from main import app


def _chat_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    return resp


class RecordingAnalysisTest(unittest.TestCase):
    """POST /v1/analysis/recording — 인물·민감·3-A·요약·④ 5콜을 목킹해 통합 매핑을 검증."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @patch("app.external_api.openai_client.OpenAI")
    def test_combined_analysis_maps_all_sections(self, mock_openai: MagicMock) -> None:
        seg_id = str(uuid.uuid4())
        client = MagicMock()
        # 호출 순서: (대상자 화자 식별 폴백) → 인물 → 민감 → 3-A 기억 → ④ 언어스타일 → 요약
        # subjectSpeakerLabel 미제공이라 전사문 호칭 기반 LLM 폴백이 먼저 돈다.
        client.chat.completions.create.side_effect = [
            _chat_response('{"subjectSpeakerLabel": "SPK_1"}'),
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
                "subjectContext": {"subject": {"name": "최말순", "addressTerm": "할머니"}},
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


class SubjectSpeakerTest(unittest.TestCase):
    """POST /v1/analysis/subject-speaker — 화자 고르기 화면에 미리 골라 둘 추천."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def _segments(self, labels: list[str]) -> list[dict]:
        return [
            {"id": str(uuid.uuid4()), "segmentIndex": i, "startMs": i * 1000,
             "endMs": i * 1000 + 900, "speakerLabel": label, "transcriptText": "밥은 먹었냐"}
            for i, label in enumerate(labels)
        ]

    @patch("app.external_api.openai_client.OpenAI")
    def test_suggests_label_from_content(self, mock_openai: MagicMock) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _chat_response('{"subjectSpeakerLabel": "SPK_0"}')
        mock_openai.return_value = client
        response = self.client.post("/v1/analysis/subject-speaker", json={
            "transcriptSegments": self._segments(["SPK_0", "SPK_1"]),
            "subjectContext": {"subject": {"name": "박영자", "addressTerm": "할머니"}},
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["subjectSpeakerLabel"], "SPK_0")

    @patch("app.external_api.openai_client.OpenAI")
    def test_unknown_label_from_llm_is_dropped(self, mock_openai: MagicMock) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _chat_response('{"subjectSpeakerLabel": "SPK_9"}')
        mock_openai.return_value = client
        response = self.client.post("/v1/analysis/subject-speaker", json={
            "transcriptSegments": self._segments(["SPK_0", "SPK_1"])})
        self.assertIsNone(response.json()["subjectSpeakerLabel"])

    @patch("app.external_api.openai_client.OpenAI")
    def test_single_speaker_needs_no_llm(self, mock_openai: MagicMock) -> None:
        response = self.client.post("/v1/analysis/subject-speaker", json={
            "transcriptSegments": self._segments(["SPK_3", "SPK_3"])})
        self.assertEqual(response.json()["subjectSpeakerLabel"], "SPK_3")
        mock_openai.assert_not_called()
