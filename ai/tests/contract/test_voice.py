import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.api.dependencies import require_internal_request
from app.main import app


class VoiceTest(unittest.TestCase):
    """POST /v1/voice/* — STT(멀티파트→텍스트)·TTS(텍스트→audio/mpeg)를 목킹 검증(무API)."""

    @classmethod
    def setUpClass(cls) -> None:
        app.dependency_overrides[require_internal_request] = lambda: None
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        app.dependency_overrides.pop(require_internal_request, None)

    @patch("app.providers.llm.OpenAI")
    def test_voice_transcription_returns_text(self, mock_openai: MagicMock) -> None:
        client = MagicMock()
        client.audio.transcriptions.create.return_value = MagicMock(text="할머니 보고 싶어요")
        mock_openai.return_value = client

        response = self.client.post(
            "/v1/voice/transcriptions",
            files={"audio_file": ("msg.wav", b"RIFFfake", "audio/wav")},
            data={"language": "ko"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["text"], "할머니 보고 싶어요")
        self.assertEqual(body["provider"], "openai")

    @patch("app.providers.tts.elevenlabs_tts.httpx.post")
    def test_speech_returns_audio_bytes(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=200, content=b"ID3fakemp3")

        response = self.client.post(
            "/v1/voice/speech",
            json={"text": "밥은 먹었냐", "voiceId": "voice-123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        self.assertEqual(response.content, b"ID3fakemp3")

    @patch("app.providers.tts.elevenlabs_tts.httpx.post")
    @patch("app.pipeline.voice.service.download_audio")
    def test_voice_clone_returns_voice_id(
        self, mock_download: MagicMock, mock_post: MagicMock
    ) -> None:
        import tempfile
        from pathlib import Path

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tmp.write(b"RIFFfakeaudio")
        tmp.close()
        mock_download.return_value = Path(tmp.name)
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"voice_id": "cloned-voice-123"}
        )

        response = self.client.post(
            "/v1/voice/clone",
            json={"name": "할머니", "sampleAudioUrl": "https://storage/sample.mp3"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["voiceId"], "cloned-voice-123")

    def test_speech_without_voice_id_returns_400(self) -> None:
        # 요청 voiceId 없고 ELEVENLABS_DEFAULT_VOICE_ID도 없으면 명시적 400.
        with patch("app.pipeline.voice.service.create_tts_provider") as provider:
            response = self.client.post("/v1/voice/speech", json={"text": "안녕"})
        provider.assert_not_called()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "MISSING_VOICE_ID")


if __name__ == "__main__":
    unittest.main()
