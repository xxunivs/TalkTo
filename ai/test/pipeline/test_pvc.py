import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

from infra.external_api.tts.elevenlabs_pvc import ElevenLabsPVC, summarize
from infra.schema.voice import PvcSamplesRequest, VoiceSampleSegment
from pipeline.s4_voice import d_pvc


class SummarizeTest(unittest.TestCase):
    """GET /v1/voices/{id} 응답 → BE 가 쓰는 상태."""

    def test_pending_until_verified(self) -> None:
        voice = {
            "fine_tuning": {"state": {"eleven_multilingual_v2": "not_started"},
                            "manual_verification_requested": True, "is_allowed_to_fine_tune": True},
            "voice_verification": {"is_verified": False, "verification_failures": []},
        }
        self.assertEqual(summarize(voice)["verification"], "pending")
        self.assertEqual(summarize(voice)["training"], "not_started")

    def test_verified_training_done_failed_rejected(self) -> None:
        verified = {"voice_verification": {"is_verified": True}, "fine_tuning": {"state": {}}}
        self.assertEqual(summarize(verified)["verification"], "verified")
        training = {"fine_tuning": {"state": {"eleven_multilingual_v2": "fine_tuning"},
                                    "progress": {"eleven_multilingual_v2": 0.4}}}
        self.assertEqual(summarize(training), {"verification": "verified", "training": "training",
                                               "progress": 0.4, "message": None})
        # 쓰지 않는 모델(flash)만 끝났으면 아직 학습 중이다
        early = {"fine_tuning": {"state": {"eleven_flash_v2_5": "fine_tuned", "eleven_v4": "queued"}}}
        self.assertEqual(summarize(early)["training"], "training")
        done = {"fine_tuning": {"state": {"eleven_flash_v2_5": "fine_tuned",
                                          "eleven_multilingual_v2": "fine_tuned"}}}
        self.assertEqual(summarize(done)["training"], "done")
        failed = {"fine_tuning": {"state": {"eleven_multilingual_v2": "failed"},
                                  "message": {"eleven_multilingual_v2": "too noisy"}}}
        self.assertEqual(summarize(failed)["training"], "failed")
        self.assertEqual(summarize(failed)["message"], "too noisy")
        rejected = {"fine_tuning": {"state": {}, "manual_verification_requested": True},
                    "voice_verification": {"is_verified": False, "verification_failures": ["x"]}}
        self.assertEqual(summarize(rejected)["verification"], "rejected")


class TrainModelChoiceTest(unittest.TestCase):
    def test_prefers_v4_then_falls_back(self) -> None:
        pvc = ElevenLabsPVC("key")
        calls = []

        def fake_request(method, path, what, **kwargs):
            calls.append((method, path, kwargs.get("json")))
            if path == "/v1/models":
                return [{"model_id": "eleven_v4", "can_be_finetuned": True},
                        {"model_id": "eleven_multilingual_v2", "can_be_finetuned": True}]
            if kwargs.get("json", {}).get("model_id") == "eleven_v4":
                from infra.error import TTSProviderError
                raise TTSProviderError("not yet")
            return {}

        with mock.patch.object(pvc, "_request", side_effect=fake_request):
            self.assertEqual(pvc.train("v1"), "eleven_multilingual_v2")
        self.assertEqual([c[2]["model_id"] for c in calls if c[1].endswith("/train")],
                         ["eleven_v4", "eleven_multilingual_v2"])


@unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg 필요")
class BuildPvcFilesTest(unittest.TestCase):
    def test_splits_into_mp3_chunks(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        rate = 22050
        t = np.arange(rate * 12) / rate
        pcm = (0.3 * np.sin(2 * np.pi * 180 * t) * ((t % 0.35) < 0.25)).astype(np.float32)
        src = tmp / "call.wav"
        with wave.open(str(src), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes((pcm * 32767).astype(np.int16).tobytes())

        def fake_download(url, *, timeout_seconds, max_bytes, temp_dir):
            dst = Path(temp_dir) / "src.wav"
            shutil.copy(src, dst)
            return dst

        url = "https://storage.example/call.wav"
        request = PvcSamplesRequest(voice_id="v", samples=[
            VoiceSampleSegment(audio_url=url, start_ms=i * 3000, end_ms=i * 3000 + 2900) for i in range(4)
        ])
        settings = mock.Mock(audio_download_timeout_seconds=10, max_audio_bytes=None)
        work = tmp / "work"
        work.mkdir()
        with mock.patch.object(d_pvc, "download_audio", fake_download), \
                mock.patch.object(d_pvc, "_CHUNK_SECONDS", 6), \
                mock.patch.object(d_pvc, "_MIN_SECONDS", 1):
            files, total = d_pvc.build_pvc_files(request, settings=settings, workdir=work)
        self.assertEqual([f.suffix for f in files], [".mp3", ".mp3"])  # 6초마다 나눔
        self.assertAlmostEqual(total, 4 * 2.74, delta=0.2)


class UpfrontDownloadTest(unittest.TestCase):
    """서명된 주소가 늦게 쓰이지 않게, 자르기 전에 녹음마다 한 번씩 먼저 다 받는다(ffmpeg 없이)."""

    def test_downloads_every_source_once_before_cutting(self) -> None:
        import subprocess

        from infra.error import InvalidVoiceSampleError

        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        a, b = "https://storage.example/a.wav", "https://storage.example/b.wav"
        request = PvcSamplesRequest(voice_id="v", samples=[
            VoiceSampleSegment(audio_url=url, start_ms=0, end_ms=3000) for url in (a, b, a, b)
        ])
        events: list[tuple[str, str]] = []

        def fake_download(url, *, timeout_seconds, max_bytes, temp_dir):
            events.append(("download", url))
            path = Path(temp_dir) / f"src_{len(events)}.wav"
            path.write_bytes(b"RIFF")
            return path

        def fake_cut(src, dst, start_ms, end_ms, *, loudnorm):
            events.append(("cut", Path(src).name))
            raise subprocess.CalledProcessError(1, "ffmpeg")

        settings = mock.Mock(audio_download_timeout_seconds=10, max_audio_bytes=None)
        with mock.patch.object(d_pvc.shutil, "which", return_value="ffmpeg"), \
                mock.patch.object(d_pvc, "download_audio", fake_download), \
                mock.patch.object(d_pvc, "_cut", fake_cut), \
                self.assertRaises(InvalidVoiceSampleError):
            d_pvc.build_pvc_files(request, settings=settings, workdir=work)

        self.assertEqual(events[:2], [("download", a), ("download", b)])
        self.assertEqual([kind for kind, _ in events[2:]], ["cut"] * 4)
        # 같은 녹음의 구간은 처음 받은 파일 하나를 같이 쓴다
        self.assertEqual([name for _, name in events[2:]],
                         ["src_1.wav", "src_2.wav", "src_1.wav", "src_2.wav"])


if __name__ == "__main__":
    unittest.main()
