"""임시 파일 폴더(AI-3): 강제 종료(OOM)로 남은 녹음·PCM 을 서버가 뜰 때 지운다."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.scratch import SCRATCH_NAME, reset_scratch_dir, scratch_dir, scratch_root
from app.setting import Settings


class ScratchDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings = Settings(temp_dir=self.tmp)

    def test_root_is_a_dedicated_subfolder(self) -> None:
        self.assertEqual(scratch_root(self.settings), self.tmp / SCRATCH_NAME)
        self.assertTrue(scratch_dir(self.settings).is_dir())
        # TEMP_DIR 이 없으면(또는 설정이 가짜 객체면) 시스템 임시 폴더 아래
        self.assertEqual(scratch_root(Settings()), Path(tempfile.gettempdir()) / SCRATCH_NAME)
        self.assertEqual(scratch_root(mock.Mock()), Path(tempfile.gettempdir()) / SCRATCH_NAME)

    def test_reset_removes_leftovers_but_not_siblings(self) -> None:
        root = scratch_dir(self.settings)
        (root / "tmpabc.m4a").write_bytes(b"audio")
        (root / "tmpabc.m4a.f32").write_bytes(b"pcm")
        work = root / "voice_clone_x"
        work.mkdir()
        (work / "raw_0.wav").write_bytes(b"wav")
        sibling = self.tmp / "keep.txt"  # TEMP_DIR 의 다른 파일은 건드리지 않는다
        sibling.write_text("keep")
        with mock.patch.dict(os.environ, {"WEB_CONCURRENCY": "1"}):
            self.assertEqual(reset_scratch_dir(self.settings), 3)
        self.assertTrue(root.is_dir())
        self.assertEqual(list(root.iterdir()), [])
        self.assertTrue(sibling.exists())

    def test_reset_is_skipped_with_several_workers(self) -> None:
        root = scratch_dir(self.settings)
        (root / "in_use.m4a").write_bytes(b"audio")
        with mock.patch.dict(os.environ, {"WEB_CONCURRENCY": "2"}):
            self.assertEqual(reset_scratch_dir(self.settings), 0)
        self.assertTrue((root / "in_use.m4a").exists())


class StartupCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _start(self, env: str) -> Path:
        from main import create_app

        settings = Settings(app_env=env, ai_server_token="t", temp_dir=self.tmp)
        leftover = scratch_dir(settings) / "tmpleft.m4a"
        leftover.write_bytes(b"audio")
        with mock.patch("main.get_settings", return_value=settings), \
                mock.patch.dict(os.environ, {"WEB_CONCURRENCY": "1"}):
            create_app()
        return leftover

    def test_deployed_start_wipes_leftovers(self) -> None:
        self.assertFalse(self._start("production").exists())

    def test_local_start_keeps_files(self) -> None:
        self.assertTrue(self._start("local").exists())


class DecodeToRawCleanupTest(unittest.TestCase):
    """ffmpeg 가 PCM 을 쓰다 실패하면(디스크 참 등) 반쯤 쓴 .f32 를 지운다."""

    def test_partial_pcm_is_removed_on_failure(self) -> None:
        from pipeline.s1_transcription.b_speaker_id import decode_to_raw

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        audio = tmp / "call.m4a"
        audio.write_bytes(b"x")
        partial = tmp / "call.m4a.f32"

        def failing_ffmpeg(args, **kwargs):
            partial.write_bytes(b"\0" * 64)
            raise subprocess.CalledProcessError(1, args, stderr=b"No space left on device")

        with mock.patch("subprocess.run", side_effect=failing_ffmpeg), \
                self.assertRaises(subprocess.CalledProcessError):
            decode_to_raw(audio)
        self.assertFalse(partial.exists())


if __name__ == "__main__":
    unittest.main()
