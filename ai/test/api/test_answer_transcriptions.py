"""POST /v1/voice/answer-transcriptions — 말로 답하기(ElevenLabs Scribe, 화자 나누기 없이 한국어).

녹음은 임시 파일로만 두고 바로 지우며, ElevenLabs 의 받아쓰기 기록도 바로 지운다. 말소리가 없으면
422 NO_SPEECH. 실제 API 는 부르지 않는다.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from main import app

PATH = "/v1/voice/answer-transcriptions"


def _scribe(text: str, words: list[dict] | None = None, tid: str = "tr_answer") -> httpx.Response:
    body = {"text": text, "words": words or [], "transcription_id": tid}
    return httpx.Response(200, json=body)


def _word(text: str, start: float, end: float, type_: str = "word") -> dict:
    return {"text": text, "start": start, "end": end, "type": type_}


class AnswerTranscriptionTest(unittest.TestCase):
    def setUp(self) -> None:
        from infra.setting import Settings, get_settings

        self.tmp = Path(tempfile.mkdtemp(prefix="answer_stt_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        settings = Settings(elevenlabs_api_key="e", fish_audio_api_key="f", temp_dir=self.tmp)
        app.dependency_overrides[get_settings] = lambda: settings
        self.addCleanup(app.dependency_overrides.pop, get_settings, None)
        self.client = TestClient(app)
        # 보낸 녹음 파일이 받아쓰기 동안 있었는지(그리고 뒤에 지워졌는지) 본다
        self.seen: list[Path] = []

    def _post(self, content: bytes = b"\x1aE\xdf\xa3webm", name: str = "answer.webm"):
        return self.client.post(PATH, files={"audio_file": (name, content, "audio/webm")})

    def _leftovers(self) -> list[Path]:
        return [p for p in self.tmp.rglob("*") if p.is_file()]

    def _fake_post(self, response: httpx.Response):
        def post(url, *, headers, files, data, timeout):
            name, handle = files["file"]
            self.seen.append(Path(handle.name))
            self.assertTrue(Path(handle.name).exists())
            self.sent = {"url": url, "data": data, "name": name, "timeout": timeout}
            return response

        return post

    @patch("infra.external_api.stt.elevenlabs_scribe.forget_transcript")
    def test_returns_plain_korean_text_and_keeps_nothing(self, forget: MagicMock) -> None:
        words = [
            _word("밥은", 0.0, 0.5),
            _word(" ", 0.5, 0.5, "spacing"),
            _word("먹었냐", 0.5, 1.2),
            _word("(웃음)", 1.2, 1.5, "audio_event"),
            _word(" ", 1.5, 1.5, "spacing"),
            _word("하셨어요", 1.6, 2.0),
        ]
        with patch(
            "infra.external_api.stt.elevenlabs_scribe.httpx.post",
            side_effect=self._fake_post(_scribe("밥은 먹었냐 하셨어요", words)),
        ):
            response = self._post()

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["text"], "밥은 먹었냐 하셨어요")
        self.assertEqual(body["provider"], "elevenlabs")
        # 화자 나누기 없이, 한국어로, Scribe 에 보낸다
        self.assertTrue(self.sent["url"].endswith("/v1/speech-to-text"))
        self.assertEqual(self.sent["data"]["diarize"], "false")
        self.assertEqual(self.sent["data"]["language_code"], "ko")
        self.assertNotIn("num_speakers", self.sent["data"])
        # ElevenLabs 의 받아쓰기 기록을 바로 지운다
        forget.assert_called_once()
        self.assertEqual(forget.call_args.args[1]["transcription_id"], "tr_answer")
        # 녹음 파일은 받아쓰기 동안만 있고 지워졌다
        self.assertEqual(len(self.seen), 1)
        self.assertFalse(self.seen[0].exists())
        self.assertEqual(self._leftovers(), [])

    @patch("infra.external_api.stt.elevenlabs_scribe.forget_transcript")
    def test_no_speech_is_a_coded_error(self, forget: MagicMock) -> None:
        for answer in (_scribe(""), _scribe("(침묵)"), _scribe("  ", [_word(" ", 0, 0, "spacing")])):
            with patch(
                "infra.external_api.stt.elevenlabs_scribe.httpx.post",
                side_effect=self._fake_post(answer),
            ):
                response = self._post()
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["error"]["code"], "NO_SPEECH")
        # 말소리가 없어도 받아쓰기 기록은 지운다
        self.assertEqual(forget.call_count, 3)
        self.assertEqual(self._leftovers(), [])

    @patch("infra.external_api.stt.elevenlabs_scribe.forget_transcript")
    def test_provider_failure_still_removes_the_recording(self, _forget: MagicMock) -> None:
        with patch(
            "infra.external_api.stt.elevenlabs_scribe.httpx.post",
            side_effect=self._fake_post(httpx.Response(500, text="scribe down")),
        ):
            response = self._post(name="answer.m4a")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "STT_PROVIDER_ERROR")
        self.assertEqual(self.seen[0].suffix, ".m4a")
        self.assertFalse(self.seen[0].exists())
        self.assertEqual(self._leftovers(), [])

    def test_too_large_is_413_before_calling_scribe(self) -> None:
        from pipeline.s4_voice.d_answer_transcription import ANSWER_AUDIO_MAX_BYTES

        with patch("infra.external_api.stt.elevenlabs_scribe.httpx.post") as post:
            response = self._post(b"\x00" * (ANSWER_AUDIO_MAX_BYTES + 1))
        self.assertEqual(response.status_code, 413)
        post.assert_not_called()

    def test_needs_the_elevenlabs_key(self) -> None:
        from infra.setting import Settings, get_settings

        settings = Settings(fish_audio_api_key="f", temp_dir=self.tmp)
        app.dependency_overrides[get_settings] = lambda: settings
        with patch("infra.external_api.stt.elevenlabs_scribe.httpx.post") as post:
            response = self._post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "provider_not_configured")
        post.assert_not_called()

    def test_odd_file_names_fall_back_to_webm(self) -> None:
        from pipeline.s4_voice.d_answer_transcription import _suffix

        self.assertEqual(_suffix("answer.WAV"), ".wav")
        self.assertEqual(_suffix("../../etc/passwd"), ".webm")
        self.assertEqual(_suffix("answer.weird-long-suffix"), ".webm")


if __name__ == "__main__":
    unittest.main()
