import unittest

from pipeline.s1_transcription.b_speaker_id import SpeakerMatch, cosine_similarity, identify_subject


class CosineSimilarityTest(unittest.TestCase):
    def test_identical_vectors(self) -> None:
        self.assertAlmostEqual(cosine_similarity([1.0, 2.0], [1.0, 2.0]), 1.0)

    def test_orthogonal_vectors(self) -> None:
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_zero_vector_is_zero(self) -> None:
        self.assertEqual(cosine_similarity([0.0, 0.0], [1.0, 1.0]), 0.0)


class IdentifySubjectTest(unittest.TestCase):
    embeddings = {
        "SPK_0": [1.0, 0.0],
        "SPK_1": [0.6, 0.8],
    }

    def test_picks_most_similar_above_threshold(self) -> None:
        match = identify_subject(self.embeddings, [0.7, 0.7], threshold=0.5)

        self.assertEqual(
            match, SpeakerMatch(speaker_label="SPK_1", similarity=match.similarity)
        )
        self.assertGreater(match.similarity, 0.9)

    def test_returns_none_below_threshold(self) -> None:
        match = identify_subject(self.embeddings, [-1.0, 0.0], threshold=0.5)

        self.assertIsNone(match)

    def test_returns_none_for_empty_speakers(self) -> None:
        self.assertIsNone(identify_subject({}, [1.0, 0.0], threshold=0.5))


if __name__ == "__main__":
    unittest.main()


class ClipReadingTest(unittest.TestCase):
    """녹음을 파일로 풀고 구간만 읽어도(read_clip) 통째로 펼쳐 자른 것(slice_ms)과 같다.

    통째로 펼치면 긴 통화에서 AI 컨테이너가 메모리 부족으로 죽었다(2026-10-18)."""

    def setUp(self) -> None:
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        try:
            import torch  # noqa: F401
        except ImportError:
            self.skipTest("torch 없음")
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg 없음")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.audio = Path(folder.name) / "tone.m4a"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
             "sine=frequency=330:duration=5", "-c:a", "aac", str(self.audio)],
            check=True,
        )

    def test_matches_full_decode_and_cleans_up(self) -> None:
        import torch

        from pipeline.s1_transcription.b_speaker_id import (
            decode_to_raw,
            load_mono_16k,
            read_clip,
            slice_ms,
        )

        full = load_mono_16k(self.audio)
        raw = decode_to_raw(self.audio)
        try:
            for start, end in ((0, 1000), (1234, 3456), (4500, None), (4900, 9000), (9000, 9500)):
                with self.subTest(start=start, end=end):
                    self.assertTrue(torch.equal(read_clip(raw, start, end), slice_ms(full, start, end)))
        finally:
            raw.unlink()

    def test_embed_speakers_leaves_no_raw_file(self) -> None:
        from infra.schema.transcript import TranscriptSegment
        from pipeline.s1_transcription.b_speaker_id import SpeakerEmbedder

        embedder = SpeakerEmbedder()
        seen = []
        embedder.embed_waveform = lambda waveform: seen.append(waveform.shape[1]) or [1.0]
        segments = [
            TranscriptSegment(segment_index=0, start_ms=0, end_ms=2000, speaker_label="SPK_0",
                              transcript_text="x"),
            TranscriptSegment(segment_index=1, start_ms=2000, end_ms=4500, speaker_label="SPK_1",
                              transcript_text="x"),
        ]
        out = embedder.embed_speakers(self.audio, segments)
        self.assertEqual(sorted(out), ["SPK_0", "SPK_1"])
        self.assertEqual(seen, [32000, 40000])
        self.assertEqual(list(self.audio.parent.glob("*.f32")), [])

    def test_long_turns_are_cut_to_the_speaker_budget(self) -> None:
        """혼자 몇 분씩 이어 말한 구간도 화자당 한도(운영 60초)까지만 넣는다(메모리가 길이만큼 는다)."""
        from unittest.mock import patch

        from infra.schema.transcript import TranscriptSegment
        from pipeline.s1_transcription import b_speaker_id
        from pipeline.s1_transcription.b_speaker_id import SpeakerEmbedder

        embedder = SpeakerEmbedder()
        seen = []
        embedder.embed_waveform = lambda waveform: seen.append(waveform.shape[1]) or [1.0]
        segments = [
            TranscriptSegment(segment_index=0, start_ms=0, end_ms=1500, speaker_label="SPK_0",
                              transcript_text="x"),
            TranscriptSegment(segment_index=1, start_ms=1500, end_ms=4800, speaker_label="SPK_0",
                              transcript_text="x"),
            TranscriptSegment(segment_index=2, start_ms=0, end_ms=4800, speaker_label="SPK_1",
                              transcript_text="x"),
        ]
        with patch.object(b_speaker_id, "MAX_SPEECH_SECONDS", 2.0):
            embedder.embed_speakers(self.audio, segments)
            embedder.embed_clip(self.audio)
        # SPK_0: 1.5초 + 남은 0.5초, SPK_1: 4.8초 구간에서 2초만, 기준 클립도 2초만
        self.assertEqual(seen, [32000, 32000, 32000])
