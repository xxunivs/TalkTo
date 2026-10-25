import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx

from infra.error import AudioDownloadFailedError, AudioUrlExpiredError
from infra.external_api.audio import download_audio

_URL = "https://storage.example.com/a.m4a?X-Amz-Signature=SECRETSIG"


def _fake_stream(status_code: int, chunks: list):
    """httpx.stream 대역. chunks 에 예외가 있으면 그 자리에서 던진다(받다가 끊김)."""

    class Response:
        def __init__(self) -> None:
            self.status_code = status_code

        def iter_bytes(self):
            for chunk in chunks:
                if isinstance(chunk, BaseException):
                    raise chunk
                yield chunk

    @contextmanager
    def stream(_method, _url, **_kwargs):
        yield Response()

    return stream


class DownloadAudioCleanupTest(unittest.TestCase):
    """어떤 실패든 받다 만 임시 파일을 지우고 다시 던진다. httpx 오류는 AppError 로 바꾼다."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _download(self, status_code: int, chunks: list) -> Path:
        with patch("infra.external_api.audio.httpx.stream", _fake_stream(status_code, chunks)):
            return download_audio(_URL, temp_dir=self.tmp)

    def test_success_keeps_file(self) -> None:
        path = self._download(200, [b"abc", b"def"])
        self.assertEqual(path.read_bytes(), b"abcdef")

    def test_interrupt_mid_stream_removes_partial_file(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            self._download(200, [b"abc", KeyboardInterrupt()])
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_unexpected_error_mid_stream_removes_partial_file(self) -> None:
        with self.assertRaises(OSError):
            self._download(200, [b"abc", OSError("disk full")])
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_httpx_error_is_wrapped_without_url(self) -> None:
        with self.assertRaises(AudioDownloadFailedError) as caught:
            self._download(200, [b"abc", httpx.ReadError("connection reset")])
        self.assertIn("ReadError", caught.exception.message)
        self.assertNotIn("SECRETSIG", caught.exception.message)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_expired_url_removes_file(self) -> None:
        with self.assertRaises(AudioUrlExpiredError):
            self._download(403, [])
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_size_limit_removes_file(self) -> None:
        with patch("infra.external_api.audio.httpx.stream", _fake_stream(200, [b"x" * 10])):
            with self.assertRaises(AudioDownloadFailedError):
                download_audio(_URL, temp_dir=self.tmp, max_bytes=5)
        self.assertEqual(list(self.tmp.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
