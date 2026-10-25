"""목소리 재료: 혼자 한 시간 넘게 말한 구간 하나도 앞 60초만 잘라 잰다(메모리 상한, AI-1).

예전에는 품질을 재려고 구간 전체를 44.1kHz 로 잘라 메모리에 펼쳤다(10분에 약 290MB). 한 시간짜리
구간 하나로 AI 컨테이너(2.5GB)가 강제 종료될 수 있었다.
"""

import shutil
import subprocess
import tempfile
import tracemalloc
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

from infra.schema.voice import VoiceSampleSegment
from infra.scratch import scratch_root
from pipeline.s4_voice import a_voice
from pipeline.s4_voice import c_sample_quality as quality


def _write_wav(path: Path, seconds: float, rate: int) -> None:
    t = np.arange(int(seconds * rate)) / rate
    pcm = (0.3 * np.sin(2 * np.pi * 180 * t) * ((t % 0.35) < 0.25)).astype(np.float32)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((pcm * 32767).astype(np.int16).tobytes())


class MeasureWavLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_reads_only_the_first_max_seconds(self) -> None:
        path = self.tmp / "long.wav"
        _write_wav(path, 10, 8000)
        self.assertAlmostEqual(quality.measure_wav(path).seconds, 10.0, places=2)
        self.assertAlmostEqual(quality.measure_wav(path, max_seconds=4).seconds, 4.0, places=2)

    def test_peak_memory_is_bounded_by_max_seconds(self) -> None:
        # 120초 44.1kHz wav 를 10초만 재면 메모리도 10초어치만 쓴다(통째로 읽으면 약 60MB)
        path = self.tmp / "two_minutes.wav"
        _write_wav(path, 120, 44100)
        tracemalloc.start()
        try:
            q = quality.measure_wav(path, max_seconds=10)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertAlmostEqual(q.seconds, 10.0, places=2)
        self.assertLess(peak, 15 * 1024 * 1024)


class BuildMultiSampleCapTest(unittest.TestCase):
    """ffmpeg 없이: 자르기·이어붙이기를 가짜로 바꾸고 '얼마나 길게 자르라고 했는지'만 본다."""

    def test_two_hour_turn_is_measured_and_used_within_60_seconds(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        cuts: list[tuple[int, int, bool]] = []

        def fake_download(url, *, timeout_seconds, max_bytes, temp_dir):
            path = Path(temp_dir) / "src.m4a"
            path.write_bytes(b"x")
            return path

        def fake_cut(src, dst, start_ms, end_ms, *, loudnorm=False):
            cuts.append((start_ms, end_ms, loudnorm))
            _write_wav(dst, min((end_ms - start_ms) / 1000, 2.0), 8000)  # 재기용 작은 wav

        settings = mock.Mock(audio_download_timeout_seconds=10, max_audio_bytes=None, temp_dir=tmp)
        segment = VoiceSampleSegment(
            audio_url="https://s/monologue.m4a", start_ms=0, end_ms=2 * 60 * 60 * 1000
        )
        with (
            mock.patch.object(a_voice.shutil, "which", return_value="ffmpeg"),
            mock.patch.object(a_voice, "download_audio", fake_download),
            mock.patch.object(a_voice, "_ffmpeg_normalize", fake_cut),
            mock.patch.object(a_voice.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)),
            mock.patch.object(a_voice, "_CLONE_MIN_SAMPLE_SECONDS", 1.0),
        ):
            out = a_voice._build_multi_sample([segment], settings=settings)
        self.addCleanup(shutil.rmtree, out.parent, True)

        measure_cuts = [c for c in cuts if not c[2]]
        final_cuts = [c for c in cuts if c[2]]
        self.assertEqual(len(measure_cuts), 1)
        start, end, _ = measure_cuts[0]
        self.assertLessEqual(end - start, 60_000)  # 예전: 2시간 전체를 잘라 펼쳤다
        self.assertTrue(final_cuts)
        for start, end, _ in final_cuts:
            self.assertLessEqual(end - start, 60_000)
        # 작업 폴더는 서버가 뜰 때 비우는 임시 폴더(talkto-ai) 아래에 둔다(AI-3)
        self.assertEqual(out.parent.parent, scratch_root(settings))


if __name__ == "__main__":
    unittest.main()
