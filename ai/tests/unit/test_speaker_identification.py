import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.config import load_settings
from app.pipeline.speaker_id.service import SpeakerMatch
from app.pipeline.transcription.service import _identify_subject_speaker
from app.schemas.transcript import TranscriptionRequest, TranscriptSegment

_SEGMENTS = [
    TranscriptSegment(
        segmentIndex=0, startMs=0, endMs=2000, speakerLabel="SPK_0", transcriptText="응 그래"
    ),
    TranscriptSegment(
        segmentIndex=1, startMs=2000, endMs=4000, speakerLabel="SPK_1", transcriptText="밥 먹었어?"
    ),
]


def _request(reference: str | None) -> TranscriptionRequest:
    return TranscriptionRequest(
        jobId="00000000-0000-0000-0000-000000000001",
        recordingId="00000000-0000-0000-0000-000000000002",
        audioUrl="https://storage/recording.m4a",
        referenceVoiceSampleUrl=reference,
    )


class SpeakerIdentificationTest(unittest.TestCase):
    """전사 단계의 대상자 화자 식별 — ECAPA 매칭을 목킹해 배선을 검증(무torch)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = load_settings(".env")

    def test_no_reference_skips_identification(self) -> None:
        # 참조 샘플이 없으면 다운로드·매칭 없이 바로 None.
        label = _identify_subject_speaker(
            Path("/tmp/rec.m4a"), _SEGMENTS, _request(None), self.settings
        )
        self.assertIsNone(label)

    @patch("app.pipeline.transcription.service.download_audio")
    @patch("app.pipeline.speaker_id.service.identify_subject")
    @patch("app.pipeline.speaker_id.service.SpeakerEmbedder")
    def test_matched_speaker_returned(
        self, mock_embedder: MagicMock, mock_identify: MagicMock, mock_download: MagicMock
    ) -> None:
        mock_download.return_value = Path("/tmp/ref.m4a")
        embedder = mock_embedder.return_value
        embedder.embed_clip.return_value = [0.1, 0.2]
        embedder.embed_speakers.return_value = {"SPK_0": [0.1, 0.2], "SPK_1": [0.9, 0.0]}
        mock_identify.return_value = SpeakerMatch(speaker_label="SPK_0", similarity=0.88)

        label = _identify_subject_speaker(
            Path("/tmp/rec.m4a"), _SEGMENTS, _request("https://storage/ref.m4a"), self.settings
        )
        self.assertEqual(label, "SPK_0")

    @patch("app.pipeline.transcription.service.download_audio")
    @patch("app.pipeline.speaker_id.service.identify_subject")
    @patch("app.pipeline.speaker_id.service.SpeakerEmbedder")
    def test_below_threshold_returns_none(
        self, mock_embedder: MagicMock, mock_identify: MagicMock, mock_download: MagicMock
    ) -> None:
        mock_download.return_value = Path("/tmp/ref.m4a")
        embedder = mock_embedder.return_value
        embedder.embed_clip.return_value = [0.1]
        embedder.embed_speakers.return_value = {"SPK_0": [0.1]}
        mock_identify.return_value = None  # 임계값 미달 → 미확정

        label = _identify_subject_speaker(
            Path("/tmp/rec.m4a"), _SEGMENTS, _request("https://storage/ref.m4a"), self.settings
        )
        self.assertIsNone(label)


if __name__ == "__main__":
    unittest.main()
