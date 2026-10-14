import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

from infra.schema.voice import VoiceSampleSegment
from pipeline.s4_voice import a_voice
from pipeline.s4_voice import c_sample_quality as quality

RATE = 22050


def _speech_like(seconds: float, *, noise: float = 0.0, gain: float = 0.3, seed: int = 0) -> np.ndarray:
    """말소리 흉내: 0.25초 소리 · 0.1초 쉼을 되풀이. noise 는 늘 깔리는 잡음 크기."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    on = (t % 0.35) < 0.25
    voice = gain * np.sin(2 * np.pi * 180 * t) * on
    return np.clip(voice + noise * rng.standard_normal(len(t)), -1, 1).astype(np.float32)


def _write(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((pcm * 32767).astype(np.int16).tobytes())


class MeasureTest(unittest.TestCase):
    def test_clean_noisy_clipped_quiet(self) -> None:
        clean = quality.measure(_speech_like(3), RATE)
        noisy = quality.measure(_speech_like(3, noise=0.12), RATE)
        clipped = quality.measure(_speech_like(3, gain=3.0), RATE)
        quiet = quality.measure(_speech_like(3, gain=0.002), RATE)
        self.assertTrue(clean.ok, clean)
        self.assertFalse(noisy.ok, noisy)
        self.assertLess(noisy.snr_db, clean.snr_db)
        self.assertFalse(clipped.ok, clipped)
        self.assertFalse(quiet.ok, quiet)
        self.assertAlmostEqual(clean.seconds, 3, places=1)


class PickTest(unittest.TestCase):
    def _q(self, seconds: float, snr: float) -> quality.SegmentQuality:
        return quality.SegmentQuality(seconds=seconds, snr_db=snr, clip_ratio=0.0, level_db=-20)

    def test_drops_bad_and_alternates_recordings(self) -> None:
        cands = [
            ("a", self._q(5, 30)),
            ("a", self._q(5, 25)),
            ("a", self._q(5, 5)),  # 잡음 많음
            ("b", self._q(5, 28)),
        ]
        self.assertEqual(quality.pick(cands, budget_seconds=10, min_seconds=5), [0, 3])
        self.assertEqual(quality.pick(cands, budget_seconds=100, min_seconds=5), [0, 1, 3])

    def test_falls_back_when_everything_is_noisy(self) -> None:
        """통화 녹음처럼 전부 잡음이 있어도 점수 높은 것부터 채워 복제는 되게 한다."""
        cands = [("a", self._q(6, 8)), ("a", self._q(6, 10)), ("a", self._q(6, 3))]
        self.assertEqual(quality.pick(cands, budget_seconds=12, min_seconds=10), [0, 1])


@unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg 필요")
class BuildMultiSampleTest(unittest.TestCase):
    def test_keeps_clean_segments_and_downloads_each_recording_once(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        # 녹음 하나: 앞 4초는 깨끗, 뒤 4초는 잡음투성이
        recording = tmp / "call.wav"
        _write(recording, np.concatenate([_speech_like(4), _speech_like(4, noise=0.15, seed=1)]))
        downloads: list[str] = []

        def fake_download(url, *, timeout_seconds, max_bytes, temp_dir):
            downloads.append(url)
            dst = Path(temp_dir) / f"src_{len(downloads)}.wav"
            shutil.copy(recording, dst)
            return dst

        url = "https://storage.example/call.wav"
        segments = [
            VoiceSampleSegment(audio_url=url, start_ms=0, end_ms=4000),
            VoiceSampleSegment(audio_url=url, start_ms=4000, end_ms=8000),
        ]
        settings = mock.Mock(audio_download_timeout_seconds=10, max_audio_bytes=10**9)
        with mock.patch.object(a_voice, "download_audio", fake_download), \
                mock.patch.object(a_voice, "_CLONE_MIN_SAMPLE_SECONDS", 2.0):
            out = a_voice._build_multi_sample(segments, settings=settings)
        self.addCleanup(shutil.rmtree, out.parent, True)
        self.assertEqual(len(downloads), 1)  # 같은 녹음은 한 번만 내려받는다
        with wave.open(str(out), "rb") as w:
            seconds = w.getnframes() / w.getframerate()
        # 깨끗한 앞 구간(앞뒤 0.08초씩 깎음)만 들어간다
        self.assertAlmostEqual(seconds, 3.84, delta=0.1)


if __name__ == "__main__":
    unittest.main()
