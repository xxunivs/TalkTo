import json
import unittest
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from main import app


class VoiceTest(unittest.TestCase):
    """POST /v1/voice/* — STT(멀티파트→텍스트)·TTS(텍스트→audio/mpeg)를 목킹 검증(무API)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @patch("infra.external_api.openai_client.OpenAI")
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

    @patch("infra.external_api.tts.elevenlabs.httpx.post")
    def test_speech_returns_audio_bytes(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=200, content=b"ID3fakemp3")

        response = self.client.post(
            "/v1/voice/speech",
            json={"text": "밥은 먹었냐", "voiceId": "voice-123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        self.assertEqual(response.content, b"ID3fakemp3")

    @patch("infra.external_api.tts.elevenlabs.httpx.post")
    @patch("pipeline.s4_voice.a_voice.download_audio")
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
        # 로컬 .env 의 TTS_PROVIDER 와 무관하게 ElevenLabs 클론 경로를 본다
        from infra.setting import Settings, get_settings

        app.dependency_overrides[get_settings] = lambda: Settings(
            tts_provider="elevenlabs", elevenlabs_api_key="e"
        )
        self.addCleanup(app.dependency_overrides.pop, get_settings, None)

        response = self.client.post(
            "/v1/voice/clone",
            json={"name": "할머니", "sampleAudioUrl": "https://storage/sample.mp3"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["voiceId"], "cloned-voice-123")

    def test_speech_without_voice_id_returns_400(self) -> None:
        # 요청 voiceId 없고 ELEVENLABS_DEFAULT_VOICE_ID도 없으면 명시적 400.
        with patch("pipeline.s4_voice.a_voice.create_tts_provider") as provider:
            response = self.client.post("/v1/voice/speech", json={"text": "안녕"})
        provider.assert_not_called()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "MISSING_VOICE_ID")


if __name__ == "__main__":
    unittest.main()


class ElevenLabsModelTest(unittest.TestCase):
    """v4 에는 style·speed 를 보내지 않고, v4 학습 안 된 PVC 는 v2 로 되돌린다."""

    def setUp(self) -> None:
        from infra.external_api.tts.elevenlabs import ElevenLabsTTSProvider

        ElevenLabsTTSProvider._v4_cache.clear()
        ElevenLabsTTSProvider._v4_checked_at.clear()

    def _provider(self, model_id: str):
        from infra.external_api.tts.elevenlabs import ElevenLabsTTSProvider

        return ElevenLabsTTSProvider(api_key="k", model_id=model_id)

    @patch("infra.external_api.tts.elevenlabs.httpx.get")
    @patch("infra.external_api.tts.elevenlabs.httpx.post")
    def test_v4_sends_only_supported_settings(self, mock_post, mock_get) -> None:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"category": "cloned"})
        mock_post.return_value = MagicMock(status_code=200, content=b"ID3")
        result = self._provider("eleven_v4_turbo").synthesize("밥은 먹었냐", voice_id="ivc-1")
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["model_id"], "eleven_v4_turbo")
        self.assertEqual(set(body["voice_settings"]), {"stability", "similarity_boost"})
        self.assertEqual(result.model, "eleven_v4_turbo")

    @patch("infra.external_api.tts.elevenlabs.httpx.get")
    @patch("infra.external_api.tts.elevenlabs.httpx.post")
    def test_pvc_without_v4_training_falls_back_to_v2(self, mock_post, mock_get) -> None:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            "category": "professional",
            "fine_tuning": {"state": {"eleven_multilingual_v2": "fine_tuned"}},
        })
        mock_post.return_value = MagicMock(status_code=200, content=b"ID3")
        self._provider("eleven_v4_turbo").synthesize("밥은 먹었냐", voice_id="pvc-1")
        self._provider("eleven_v4_turbo").synthesize("또 왔냐", voice_id="pvc-1")
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["model_id"], "eleven_multilingual_v2")
        self.assertIn("style", body["voice_settings"])
        self.assertEqual(mock_get.call_count, 1)  # 요청마다 provider 가 새로 생겨도 한 번만 묻는다

    @patch("infra.external_api.tts.elevenlabs.httpx.get")
    def test_pvc_with_v4_training_uses_v4(self, mock_get) -> None:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            "category": "professional",
            "fine_tuning": {"state": {"eleven_v4": "fine_tuned"}},
        })
        self.assertEqual(self._provider("eleven_v4_turbo").model_for("pvc-2"), "eleven_v4_turbo")

    @patch("infra.external_api.tts.elevenlabs.httpx.get")
    def test_v2_does_not_check_training(self, mock_get) -> None:
        self.assertEqual(self._provider(None).model_for("any"), "eleven_multilingual_v2")
        mock_get.assert_not_called()


class MiniMaxProviderTest(unittest.TestCase):
    """MiniMax: 업로드→클론 등록→첫 사용, 합성은 hex 음성을 bytes 로. 목소리 id 로 회사를 고른다."""

    def _provider(self, **kwargs):
        from infra.external_api.tts.minimax import MiniMaxTTSProvider

        return MiniMaxTTSProvider(api_key="k", **kwargs)

    @staticmethod
    def _ok(**extra):
        return MagicMock(status_code=200, json=lambda: {"base_resp": {"status_code": 0}, **extra})

    @patch("infra.external_api.tts.minimax.httpx.post")
    def test_synthesize_decodes_hex_and_leaves_emotion_to_minimax(self, mock_post) -> None:
        mock_post.return_value = self._ok(data={"audio": b"ID3".hex()})
        result = self._provider().synthesize("밥은 먹었냐", voice_id="TalkToabc", speed=0.9)
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(result.audio, b"ID3")
        self.assertEqual(result.provider, "minimax")
        self.assertEqual(body["language_boost"], "Korean")
        self.assertEqual(body["voice_setting"]["speed"], 0.9)
        self.assertNotIn("emotion", body["voice_setting"])
        self.assertNotIn("voice_modify", body)

    @patch("infra.external_api.tts.minimax.httpx.post")
    def test_error_inside_200_is_raised(self, mock_post) -> None:
        from infra.error import TTSProviderError

        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"base_resp": {"status_code": 2013, "status_msg": "invalid"}})
        with self.assertRaises(TTSProviderError):
            self._provider().synthesize("안녕", voice_id="TalkToabc")

    @patch("infra.external_api.tts.minimax.httpx.post")
    def test_clone_uploads_registers_and_uses_once(self, mock_post) -> None:
        import tempfile
        from pathlib import Path

        mock_post.side_effect = [
            self._ok(file={"file_id": 42}),
            self._ok(),
            self._ok(data={"audio": b"ID3".hex()}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.wav"
            sample.write_bytes(b"RIFF")
            voice_id = self._provider().clone_voice("할머니", sample)
        urls = [c.args[0] for c in mock_post.call_args_list]
        self.assertTrue(urls[0].endswith("/v1/files/upload"))
        self.assertTrue(urls[1].endswith("/v1/voice_clone"))
        self.assertTrue(urls[2].endswith("/v1/t2a_v2"))  # 7일 삭제를 막는 첫 사용
        self.assertTrue(voice_id.startswith("TalkTo"))
        self.assertEqual(mock_post.call_args_list[1].kwargs["json"]["file_id"], 42)
        self.assertEqual(mock_post.call_args_list[1].kwargs["json"]["voice_id"], voice_id)

    def test_speech_routes_by_voice_id(self) -> None:
        from infra.external_api.tts import create_tts_provider
        from infra.setting import Settings

        settings = Settings(tts_provider="minimax", minimax_api_key="m", elevenlabs_api_key="e")
        self.assertEqual(create_tts_provider(settings).name, "minimax")
        self.assertEqual(create_tts_provider(settings, voice_id="TalkTo123").name, "minimax")
        # 예전에 만든 ElevenLabs 목소리(PVC 포함)는 계속 ElevenLabs 로 간다
        self.assertEqual(create_tts_provider(settings, voice_id="aBcD1234efGH5678ijKL").name, "elevenlabs")


class MiniMaxEmotionTest(unittest.TestCase):
    """대답마다 고른 감정을 MiniMax 감정으로 바꿔 보내고, neutral·없음은 MiniMax 에 맡긴다."""

    @patch("infra.external_api.tts.minimax.httpx.post")
    def test_emotion_mapping(self, mock_post) -> None:
        from infra.external_api.tts.minimax import MiniMaxTTSProvider

        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"base_resp": {"status_code": 0}, "data": {"audio": "00"}})
        provider = MiniMaxTTSProvider(api_key="k")
        for ours, sent in (("happy", "happy"), ("calm", "calm"), ("neutral", None), (None, None)):
            provider.synthesize("응", voice_id="TalkTo1", emotion=ours)
            body = mock_post.call_args.kwargs["json"]
            self.assertEqual(body["voice_setting"].get("emotion"), sent, ours)
        self.assertEqual(body["model"], "speech-2.8-turbo")

    def test_speech_rejects_unknown_emotion(self) -> None:
        client = TestClient(app)
        response = client.post("/v1/voice/speech", json={"text": "응", "voiceId": "TalkTo1", "emotion": "angry"})
        self.assertIn(response.status_code, (400, 422))


class CloneProviderChoiceTest(unittest.TestCase):
    """BE 가 정해 준 회사로 목소리를 만든다(통화 녹음 → minimax)."""

    @patch("infra.external_api.tts.minimax.httpx.post")
    @patch("pipeline.s4_voice.a_voice.download_audio")
    def test_clone_uses_requested_provider(self, mock_download, mock_post) -> None:
        import tempfile
        from pathlib import Path

        from infra.setting import Settings, get_settings

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tmp.write(b"RIFFfake")
        tmp.close()
        mock_download.return_value = Path(tmp.name)
        ok = lambda **extra: MagicMock(status_code=200, json=lambda: {"base_resp": {"status_code": 0}, **extra})  # noqa: E731
        mock_post.side_effect = [ok(file={"file_id": 1}), ok(), ok(data={"audio": "00"})]
        app.dependency_overrides[get_settings] = lambda: Settings(
            tts_provider="elevenlabs", elevenlabs_api_key="e", minimax_api_key="m")
        self.addCleanup(app.dependency_overrides.pop, get_settings, None)
        response = TestClient(app).post("/v1/voice/clone", json={
            "name": "TalkTo_할머니", "sampleAudioUrl": "https://storage/a.mp3", "provider": "minimax"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["provider"], "minimax")
        self.assertTrue(response.json()["voiceId"].startswith("TalkTo"))


class VoiceDeleteTest(unittest.TestCase):
    """POST /v1/voice/delete — 목소리를 만든 회사에서 지운다(MiniMax 는 가짜 전송으로)."""

    def setUp(self) -> None:
        from infra.setting import Settings, get_settings

        app.dependency_overrides[get_settings] = lambda: Settings(
            tts_provider="elevenlabs", elevenlabs_api_key="e", minimax_api_key="m")
        self.addCleanup(app.dependency_overrides.pop, get_settings, None)
        self.sent: list[httpx.Request] = []

    def _fake_post(self, body: dict):
        """httpx.post 대역. 실제 요청 모양을 MockTransport 로 만들어 모아 두고 정한 답을 준다."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.sent.append(request)
            return httpx.Response(200, json=body)

        def post(url, **kwargs):
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                return client.post(url, **kwargs)

        return post

    def test_minimax_provider_deletes_cloned_voice(self) -> None:
        from infra.external_api.tts.minimax import MiniMaxTTSProvider

        ok = {"base_resp": {"status_code": 0, "status_msg": "success"}}
        with patch("infra.external_api.tts.minimax.httpx.post", self._fake_post(ok)):
            MiniMaxTTSProvider(api_key="k").delete_voice("TalkToabc")
        request = self.sent[0]
        self.assertEqual(str(request.url), "https://api.minimax.io/v1/delete_voice")
        self.assertEqual(request.headers["Authorization"], "Bearer k")
        self.assertEqual(
            json.loads(request.content), {"voice_type": "voice_cloning", "voice_id": "TalkToabc"})

    def test_minimax_error_inside_200_is_raised(self) -> None:
        from infra.error import TTSProviderError
        from infra.external_api.tts.minimax import MiniMaxTTSProvider

        bad = {"base_resp": {"status_code": 2054, "status_msg": "voice not found"}}
        with (
            patch("infra.external_api.tts.minimax.httpx.post", self._fake_post(bad)),
            self.assertRaises(TTSProviderError),
        ):
            MiniMaxTTSProvider(api_key="k").delete_voice("TalkToabc")

    def test_route_uses_minimax_when_asked(self) -> None:
        ok = {"base_resp": {"status_code": 0}}
        with patch("infra.external_api.tts.minimax.httpx.post", self._fake_post(ok)):
            response = TestClient(app).post(
                "/v1/voice/delete", json={"voiceId": "TalkToabc", "provider": "minimax"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(json.loads(self.sent[0].content)["voice_id"], "TalkToabc")

    @patch("pipeline.s4_voice.d_pvc.delete")
    def test_route_defaults_to_elevenlabs(self, mock_delete: MagicMock) -> None:
        response = TestClient(app).post("/v1/voice/delete", json={"voiceId": "aBcD1234efGH5678ijKL"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(mock_delete.call_args.args[0], "aBcD1234efGH5678ijKL")
        bad = TestClient(app).post("/v1/voice/delete", json={"voiceId": "x", "provider": "other"})
        self.assertIn(bad.status_code, (400, 422))


class VoiceTranscriptionLimitTest(unittest.TestCase):
    """음성 메시지는 25MiB 까지만 읽는다. 넘으면 받아쓰기를 부르지 않고 413 AUDIO_TOO_LARGE."""

    def test_limit_is_25_mib_and_route_runs_in_threadpool(self) -> None:
        import inspect

        from infra.be_ai_api.routes import voice as voice_routes

        self.assertEqual(voice_routes.VOICE_MESSAGE_MAX_BYTES, 25 * 1024 * 1024)
        self.assertFalse(inspect.iscoroutinefunction(voice_routes.create_voice_transcription))

    @patch("infra.be_ai_api.routes.voice.VOICE_MESSAGE_MAX_BYTES", 8)
    @patch("infra.be_ai_api.routes.voice.transcribe_voice_message")
    def test_over_limit_returns_413(self, mock_transcribe: MagicMock) -> None:
        response = TestClient(app).post(
            "/v1/voice/transcriptions",
            files={"audio_file": ("msg.wav", b"123456789", "audio/wav")},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "AUDIO_TOO_LARGE")
        mock_transcribe.assert_not_called()

    @patch("infra.be_ai_api.routes.voice.VOICE_MESSAGE_MAX_BYTES", 8)
    @patch("infra.be_ai_api.routes.voice.transcribe_voice_message")
    def test_at_limit_is_transcribed_whole(self, mock_transcribe: MagicMock) -> None:
        from infra.schema.voice import VoiceTranscriptionResponse

        mock_transcribe.return_value = VoiceTranscriptionResponse(
            text="응", provider="openai", model="whisper-1"
        )
        response = TestClient(app).post(
            "/v1/voice/transcriptions",
            files={"audio_file": ("msg.wav", b"12345678", "audio/wav")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(mock_transcribe.call_args.args[0], b"12345678")


class MiniMaxCloneLogTest(unittest.TestCase):
    """클론 기록에는 voice_id 만 남긴다(대상자 이름은 남기지 않는다)."""

    @patch("infra.external_api.tts.minimax.httpx.post")
    def test_clone_log_has_voice_id_but_not_name(self, mock_post) -> None:
        import tempfile
        from pathlib import Path

        from infra.external_api.tts.minimax import MiniMaxTTSProvider

        ok = lambda **extra: MagicMock(status_code=200, json=lambda: {"base_resp": {"status_code": 0}, **extra})  # noqa: E731
        mock_post.side_effect = [ok(file={"file_id": 1}), ok(), ok(data={"audio": "00"})]
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.wav"
            sample.write_bytes(b"RIFF")
            with self.assertLogs("infra.external_api.tts.minimax", level="INFO") as logs:
                voice_id = MiniMaxTTSProvider(api_key="k").clone_voice("김할머니", sample)
        text = "\n".join(logs.output)
        self.assertIn(voice_id, text)
        self.assertNotIn("김할머니", text)
