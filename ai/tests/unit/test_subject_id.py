import unittest
from unittest.mock import MagicMock, patch

from app.be_ai_api.recording_analysis import _resolve_subject_speaker_label
from app.tool.s1_transcription.b_speaker_id import identify_subject_by_content
from app.data_schema.context import SubjectContext, SubjectInfo
from app.data_schema.transcript import TranscriptSegment

_SEGMENTS = [
    TranscriptSegment(
        segmentIndex=0, startMs=0, endMs=2000, speakerLabel="SPK_1",
        transcriptText="할머니, 밥 먹었어?",
    ),
    TranscriptSegment(
        segmentIndex=1, startMs=2000, endMs=4000, speakerLabel="SPK_0",
        transcriptText="아직 안 먹었다. 너는 먹었니?",
    ),
]
_CONTEXT = SubjectContext(subject=SubjectInfo(name="신금자", addressTerm="할머니"))


def _mock_client(content: str) -> MagicMock:
    client = MagicMock()
    message = MagicMock()
    message.content = content
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=message)]
    )
    return client


class IdentifySubjectByContentTest(unittest.TestCase):
    """호칭 방향으로 대상자 화자를 판정 — LLM 목킹, 환각·오류 방어 검증."""

    @patch("app.tool.s1_transcription.b_speaker_id.create_openai_client")
    def test_returns_valid_label(self, factory: MagicMock) -> None:
        factory.return_value = _mock_client('{"subjectSpeakerLabel": "SPK_0"}')
        label = identify_subject_by_content(_SEGMENTS, _CONTEXT, settings=MagicMock())
        self.assertEqual(label, "SPK_0")

    @patch("app.tool.s1_transcription.b_speaker_id.create_openai_client")
    def test_rejects_hallucinated_label(self, factory: MagicMock) -> None:
        # 실제 등장하지 않는 화자 라벨은 인정하지 않는다.
        factory.return_value = _mock_client('{"subjectSpeakerLabel": "SPK_9"}')
        self.assertIsNone(
            identify_subject_by_content(_SEGMENTS, _CONTEXT, settings=MagicMock())
        )

    @patch("app.tool.s1_transcription.b_speaker_id.create_openai_client")
    def test_null_label_returns_none(self, factory: MagicMock) -> None:
        factory.return_value = _mock_client('{"subjectSpeakerLabel": null}')
        self.assertIsNone(
            identify_subject_by_content(_SEGMENTS, _CONTEXT, settings=MagicMock())
        )

    @patch("app.tool.s1_transcription.b_speaker_id.create_openai_client")
    def test_api_error_returns_none(self, factory: MagicMock) -> None:
        factory.side_effect = RuntimeError("boom")
        self.assertIsNone(
            identify_subject_by_content(_SEGMENTS, _CONTEXT, settings=MagicMock())
        )


class ResolveSubjectLabelTest(unittest.TestCase):
    """폴백 체인: provided(ECAPA) 우선, 없으면 LLM, 발화량 폴백 없음."""

    @patch("app.be_ai_api.recording_analysis.identify_subject_by_content")
    def test_provided_short_circuits(self, llm: MagicMock) -> None:
        label = _resolve_subject_speaker_label(
            _SEGMENTS, "SPK_0", _CONTEXT, settings=MagicMock()
        )
        self.assertEqual(label, "SPK_0")
        llm.assert_not_called()

    @patch("app.be_ai_api.recording_analysis.identify_subject_by_content")
    def test_falls_back_to_llm(self, llm: MagicMock) -> None:
        llm.return_value = "SPK_0"
        label = _resolve_subject_speaker_label(
            _SEGMENTS, None, _CONTEXT, settings=MagicMock()
        )
        self.assertEqual(label, "SPK_0")
        llm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
