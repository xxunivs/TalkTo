"""ElevenLabs 계정에 남는 기록 지우기(받아쓰기·대답 읽기) — 지어낸 응답으로 본다(네트워크를 쓰지 않는다)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from infra.external_api import elevenlabs_records as records

REPO = Path(__file__).resolve().parents[2]


def _now(action):
    action()


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        # 백그라운드 대신 바로 돌리고, 다시 해 보기 전 기다리지 않는다
        for target, value in (("in_background", _now), ("RETRY_DELAYS", (0.0, 0.0, 0.0))):
            patcher = patch.object(records, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class DeleteTest(_Base):
    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    def test_transcript_delete_url_and_key(self, mock_delete) -> None:
        mock_delete.return_value = MagicMock(status_code=200)
        self.assertTrue(records.delete_transcript("k", "tr_1"))
        self.assertEqual(
            mock_delete.call_args.args[0],
            "https://api.elevenlabs.io/v1/speech-to-text/transcripts/tr_1",
        )
        self.assertEqual(mock_delete.call_args.kwargs["headers"], {"xi-api-key": "k"})

    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    def test_retries_then_gives_up(self, mock_delete) -> None:
        mock_delete.side_effect = [httpx.ConnectError("x"), MagicMock(status_code=500),
                                   MagicMock(status_code=500)]
        self.assertFalse(records.delete_history_item("k", "h_1"))
        self.assertEqual(mock_delete.call_count, 3)

    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    def test_already_gone_counts_as_deleted(self, mock_delete) -> None:
        mock_delete.return_value = MagicMock(status_code=404)
        self.assertTrue(records.delete_history_item("k", "h_1"))
        self.assertEqual(mock_delete.call_count, 1)


class ForgetTest(_Base):
    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    def test_forget_transcript_needs_an_id(self, mock_delete) -> None:
        records.forget_transcript("k", {"text": "x"})
        mock_delete.assert_not_called()
        mock_delete.return_value = MagicMock(status_code=200)
        records.forget_transcript("k", {"text": "x", "transcription_id": "tr_9"})
        self.assertTrue(mock_delete.call_args.args[0].endswith("/transcripts/tr_9"))

    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    def test_forget_speech_uses_history_header(self, mock_delete) -> None:
        mock_delete.return_value = MagicMock(status_code=200)
        records.forget_speech("k", voice_id="v", headers=httpx.Headers({"history-item-id": "h_5"}))
        self.assertTrue(mock_delete.call_args.args[0].endswith("/v1/history/h_5"))

    @patch("infra.external_api.elevenlabs_records.httpx.get")
    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    def test_forget_speech_finds_item_by_request_id(self, mock_delete, mock_get) -> None:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"history": [
            {"history_item_id": "h_other", "request_id": "r_0"},
            {"history_item_id": "h_mine", "request_id": "r_1"},
        ]})
        mock_delete.return_value = MagicMock(status_code=200)
        records.forget_speech("k", voice_id="v", headers=httpx.Headers({"request-id": "r_1"}))
        self.assertEqual(mock_get.call_args.kwargs["params"]["voice_id"], "v")
        self.assertTrue(mock_delete.call_args.args[0].endswith("/v1/history/h_mine"))

    @patch("infra.external_api.elevenlabs_records.httpx.get")
    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    def test_forget_speech_ignores_mock_like_headers(self, mock_delete, mock_get) -> None:
        records.forget_speech("k", voice_id="v", headers=MagicMock())
        records.forget_speech("k", voice_id="v", headers=None)
        mock_delete.assert_not_called()
        mock_get.assert_not_called()


class ProviderHookTest(_Base):
    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    @patch("infra.external_api.stt.elevenlabs_scribe.httpx.post")
    def test_scribe_deletes_its_transcript(self, mock_post, mock_delete) -> None:
        from infra.external_api.stt.elevenlabs_scribe import ElevenLabsScribeProvider

        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            "text": "밥은 먹었냐", "words": [], "transcription_id": "tr_42"})
        mock_delete.return_value = MagicMock(status_code=200)
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "a.wav"
            audio.write_bytes(b"RIFFfake")
            result = ElevenLabsScribeProvider(api_key="k").transcribe(audio)
        self.assertEqual(result.segments[0].transcript_text, "밥은 먹었냐")
        self.assertTrue(mock_delete.call_args.args[0].endswith("/transcripts/tr_42"))

    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    @patch("infra.external_api.tts.elevenlabs.httpx.post")
    def test_tts_deletes_its_history_item(self, mock_post, mock_delete) -> None:
        from infra.external_api.tts.elevenlabs import ElevenLabsTTSProvider

        mock_post.return_value = MagicMock(
            status_code=200, content=b"ID3", headers=httpx.Headers({"history-item-id": "h_7"}))
        mock_delete.return_value = MagicMock(status_code=200)
        result = ElevenLabsTTSProvider(api_key="k", model_id="eleven_multilingual_v2").synthesize(
            "밥은 먹었냐", voice_id="old-voice")
        self.assertEqual(result.audio, b"ID3")
        self.assertTrue(mock_delete.call_args.args[0].endswith("/v1/history/h_7"))

    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    @patch("infra.external_api.tts.elevenlabs.httpx.post")
    def test_tts_answer_survives_failed_cleanup(self, mock_post, mock_delete) -> None:
        from infra.external_api.tts.elevenlabs import ElevenLabsTTSProvider

        mock_post.return_value = MagicMock(
            status_code=200, content=b"ID3", headers=httpx.Headers({"history-item-id": "h_8"}))
        mock_delete.side_effect = httpx.ConnectError("down")
        result = ElevenLabsTTSProvider(api_key="k", model_id="eleven_multilingual_v2").synthesize(
            "밥은 먹었냐", voice_id="old-voice")
        self.assertEqual(result.audio, b"ID3")


class PurgeToolTest(_Base):
    def setUp(self) -> None:
        super().setUp()
        spec = importlib.util.spec_from_file_location(
            "purge_elevenlabs_history", REPO / "tools" / "purge_elevenlabs_history.py")
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.pages = [
            {"history": [{"history_item_id": "h1", "date_unix": 1760000000, "source": "TTS",
                          "voice_id": "v1", "text": "비밀 글"}],
             "has_more": True, "last_history_item_id": "h1"},
            {"history": [{"history_item_id": "h2", "date_unix": 1760600000, "source": "TTS",
                          "voice_id": "v2", "text": "비밀 글 2"}],
             "has_more": False, "last_history_item_id": "h2"},
        ]

    def _get(self, pages):
        responses = iter(pages)

        def get(*args, **kwargs):
            page = next(responses)
            return MagicMock(status_code=200, json=lambda: page)

        return get

    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    @patch("infra.external_api.elevenlabs_records.httpx.get")
    def test_dry_run_counts_without_deleting_or_printing_text(self, mock_get, mock_delete) -> None:
        mock_get.side_effect = self._get(self.pages)
        lines: list[str] = []
        found, deleted = self.tool.purge("k", apply=False, out=lines.append)
        self.assertEqual((found, deleted), (2, 0))
        mock_delete.assert_not_called()
        self.assertIn("2건", lines[0])
        self.assertNotIn("비밀", "\n".join(lines))
        self.assertEqual(mock_get.call_args_list[1].kwargs["params"]["start_after_history_item_id"],
                         "h1")

    @patch("infra.external_api.elevenlabs_records.httpx.delete")
    @patch("infra.external_api.elevenlabs_records.httpx.get")
    def test_apply_deletes_every_item(self, mock_get, mock_delete) -> None:
        empty = {"history": [], "has_more": False, "last_history_item_id": None}
        mock_get.side_effect = self._get([*self.pages, empty])
        mock_delete.return_value = MagicMock(status_code=200)
        lines: list[str] = []
        found, deleted = self.tool.purge("k", apply=True, out=lines.append)
        self.assertEqual((found, deleted), (2, 2))
        self.assertEqual(
            [call.args[0].rsplit("/", 1)[-1] for call in mock_delete.call_args_list], ["h1", "h2"])
        self.assertIn("남은 기록 0건", lines[-1])


if __name__ == "__main__":
    unittest.main()
