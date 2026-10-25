import json
import re
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from main import app


def _settings(**extra):
    from infra.setting import Settings

    values = {"fish_audio_api_key": "f", "elevenlabs_api_key": "e", "minimax_api_key": "m"}
    return Settings(**{**values, **extra})


def _use_settings(test: unittest.TestCase, **extra) -> None:
    """로컬 .env 와 무관하게 정한 설정으로 라우트를 부른다."""
    from infra.setting import get_settings

    app.dependency_overrides[get_settings] = lambda: _settings(**extra)
    test.addCleanup(app.dependency_overrides.pop, get_settings, None)


def _temp_audio(test: unittest.TestCase, suffix: str = ".wav") -> Path:
    handle, name = tempfile.mkstemp(suffix=suffix)
    with open(handle, "wb") as out:
        out.write(b"RIFFfakeaudio")
    path = Path(name)
    test.addCleanup(path.unlink, missing_ok=True)
    return path


class FakeFish:
    """fish 모듈의 httpx.request·httpx.stream 대역. 실제 요청 모양(httpx.Request)을
    MockTransport 로 만들어 모아 두고, 정한 답(httpx.Response, 던질 예외, 또는 요청을 받아
    답을 주는 함수)을 차례로 준다."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.sent: list[httpx.Request] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.sent.append(request)
        answer = self.answers.pop(0)
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def request(self, method, url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(self._handle)) as client:
            return client.request(method, url, **kwargs)

    @contextmanager
    def stream(self, method, url, **kwargs):
        with (
            httpx.Client(transport=httpx.MockTransport(self._handle)) as client,
            client.stream(method, url, **kwargs) as response,
        ):
            yield response

    def patch(self):
        return patch.multiple(
            "infra.external_api.tts.fish.httpx", request=self.request, stream=self.stream)


class FakeClock:
    """fish 모듈의 time 대역. sleep 은 기다리지 않고 시계만 앞으로 돌린다."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _audio(content: bytes = b"ID3fishmp3") -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": "audio/mpeg"})


def _model(state: str = "trained", model_id: str = "m1", status: int = 201) -> httpx.Response:
    return httpx.Response(status, json={"_id": model_id, "state": state, "title": "t"})


def _fish_error(status: int) -> httpx.Response:
    return httpx.Response(status, json={"status": status, "message": "fish says no"})


def _form(request: httpx.Request) -> dict[str, list[str]]:
    """멀티파트 본문 → {이름: [값...]} (파일 칸은 파일 이름)."""
    fields: dict[str, list[str]] = {}
    pattern = rb'name="([^"]+)"(?:; filename="([^"]*)")?\r\n(?:Content-Type: [^\r]*\r\n)?\r\n(.*?)\r\n--'
    for name, filename, value in re.findall(pattern, request.content, re.DOTALL):
        fields.setdefault(name.decode(), []).append((filename or value).decode("utf-8", "ignore"))
    return fields


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
        # 예전 ElevenLabs 목소리(앞글자 없음)는 다시 만들기 전까지 ElevenLabs 로 합성한다
        mock_post.return_value = MagicMock(status_code=200, content=b"ID3fakemp3")

        response = self.client.post(
            "/v1/voice/speech",
            json={"text": "밥은 먹었냐", "voiceId": "voice-123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        self.assertEqual(response.content, b"ID3fakemp3")

    @patch("pipeline.s4_voice.a_voice.download_audio")
    def test_voice_clone_returns_fish_voice_id(self, mock_download: MagicMock) -> None:
        mock_download.return_value = _temp_audio(self, ".mp3")
        _use_settings(self)
        fake = FakeFish(_model(model_id="0123456789abcdef0123456789abcdef"))

        with fake.patch():
            response = self.client.post(
                "/v1/voice/clone",
                json={"name": "TalkTo_abcd1234", "sampleAudioUrl": "https://storage/sample.mp3"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "voiceId": "fish_0123456789abcdef0123456789abcdef", "provider": "fish"})
        self.assertEqual(str(fake.sent[0].url), "https://api.fish.audio/model")

    def test_speech_without_voice_id_returns_400(self) -> None:
        # 요청 voiceId 없고 ELEVENLABS_DEFAULT_VOICE_ID도 없으면 명시적 400.
        with patch("pipeline.s4_voice.a_voice.create_tts_provider") as provider:
            response = self.client.post("/v1/voice/speech", json={"text": "안녕"})
        provider.assert_not_called()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "MISSING_VOICE_ID")


class ElevenLabsModelTest(unittest.TestCase):
    """v4 에는 style·speed 를 보내지 않고, 즉시 복제 목소리(IVC)가 아니면 v2 로 되돌린다."""

    def setUp(self) -> None:
        from infra.external_api.tts.elevenlabs import ElevenLabsTTSProvider

        ElevenLabsTTSProvider._v4_cache.clear()

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
    def test_legacy_pvc_is_spoken_with_v2(self, mock_post, mock_get) -> None:
        # 예전 똑 닮은 목소리(PVC)는 다시 만들기 전까지 예전 ElevenLabs 목소리로 v2 합성한다
        for state in ({"eleven_multilingual_v2": "fine_tuned"}, {"eleven_v4": "fine_tuned"}):
            self.setUp()
            mock_get.return_value = MagicMock(status_code=200, json=lambda state=state: {
                "category": "professional", "fine_tuning": {"state": state}})
            mock_post.return_value = MagicMock(status_code=200, content=b"ID3")
            self._provider("eleven_v4_turbo").synthesize("밥은 먹었냐", voice_id="pvc-1")
            self._provider("eleven_v4_turbo").synthesize("또 왔냐", voice_id="pvc-1")
            body = mock_post.call_args.kwargs["json"]
            self.assertEqual(body["model_id"], "eleven_multilingual_v2")
            self.assertIn("style", body["voice_settings"])
        self.assertEqual(mock_get.call_count, 2)  # 요청마다 provider 가 새로 생겨도 목소리마다 한 번만 묻는다

    @patch("infra.external_api.tts.elevenlabs.httpx.get")
    def test_v2_does_not_check_training(self, mock_get) -> None:
        self.assertEqual(self._provider(None).model_for("any"), "eleven_multilingual_v2")
        mock_get.assert_not_called()


class MiniMaxProviderTest(unittest.TestCase):
    """MiniMax(예전 목소리): 합성은 hex 음성을 bytes 로. 목소리 id 로 회사를 고른다."""

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

    def test_legacy_providers_no_longer_clone(self) -> None:
        from infra.external_api.tts.elevenlabs import ElevenLabsTTSProvider
        from infra.external_api.tts.minimax import MiniMaxTTSProvider

        self.assertFalse(hasattr(MiniMaxTTSProvider, "clone_voice"))
        self.assertFalse(hasattr(ElevenLabsTTSProvider, "clone_voice"))


class MiniMaxEmotionTest(unittest.TestCase):
    """대답마다 고른 감정을 MiniMax 감정으로 바꿔 보내고, neutral·없음은 MiniMax 에 맡긴다."""

    @patch("infra.external_api.tts.minimax.httpx.post")
    def test_emotion_mapping(self, mock_post) -> None:
        from infra.external_api.tts.minimax import MiniMaxTTSProvider

        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {"base_resp": {"status_code": 0}, "data": {"audio": "00"}})
        provider = MiniMaxTTSProvider(api_key="k")
        for ours, sent in (("happy", "happy"), ("calm", "calm"), ("neutral", None), (None, None),
                           ("warm", None), ("worried", None)):  # Fish 용 새 이름은 MiniMax 에 없다
            provider.synthesize("응", voice_id="TalkTo1", emotion=ours)
            body = mock_post.call_args.kwargs["json"]
            self.assertEqual(body["voice_setting"].get("emotion"), sent, ours)
        self.assertEqual(body["model"], "speech-2.8-turbo")

    def test_speech_rejects_unknown_emotion(self) -> None:
        client = TestClient(app)
        response = client.post("/v1/voice/speech", json={"text": "응", "voiceId": "TalkTo1", "emotion": "angry"})
        self.assertIn(response.status_code, (400, 422))


class VoiceRoutingTest(unittest.TestCase):
    """voice_id 앞글자로 회사를 고른다: fish_ → Fish, TalkTo → MiniMax(예전), 그 밖 → ElevenLabs(예전)."""

    def test_speech_routes_by_voice_id_prefix(self) -> None:
        from infra.external_api.tts import create_tts_provider, provider_for_voice

        settings = _settings(tts_provider="minimax")
        self.assertEqual(create_tts_provider(settings, voice_id="fish_0123abcd").name, "fish")
        self.assertEqual(create_tts_provider(settings, voice_id="TalkTo123").name, "minimax")
        # 예전에 만든 ElevenLabs 목소리(PVC 포함)는 계속 ElevenLabs 로 간다
        self.assertEqual(create_tts_provider(settings, voice_id="aBcD1234efGH5678ijKL").name, "elevenlabs")
        self.assertEqual(provider_for_voice("0123456789abcdef0123456789abcdef"), "elevenlabs")
        # voice_id 가 있으면 TTS_PROVIDER 는 보지 않는다
        self.assertEqual(create_tts_provider(settings).name, "minimax")

    def test_fish_needs_its_key(self) -> None:
        from infra.error import ProviderNotConfiguredError
        from infra.external_api.tts import create_clone_provider, create_tts_provider

        settings = _settings(fish_audio_api_key=None)
        with self.assertRaises(ProviderNotConfiguredError):
            create_tts_provider(settings, voice_id="fish_abc")
        with self.assertRaises(ProviderNotConfiguredError):
            create_clone_provider(settings)

    def test_tts_provider_defaults_to_fish(self) -> None:
        from infra.external_api.tts import create_tts_provider
        from infra.setting import Settings

        self.assertEqual(Settings().tts_provider, "fish")
        self.assertEqual(create_tts_provider(_settings()).name, "fish")

    def test_fish_voice_gets_no_emotion_hint(self) -> None:
        from infra.external_api.tts import wants_emotion_hint

        self.assertFalse(wants_emotion_hint("fish_0123abcd"))
        self.assertTrue(wants_emotion_hint("TalkTo123"))


class FishSpeechTest(unittest.TestCase):
    """Fish drama-3 합성: 헤더 model 값·본문·지시 표시 지우기·한 번 다시 하기·전체 마감·오류."""

    def setUp(self) -> None:
        self.clock = FakeClock()
        clock = patch("infra.external_api.tts.fish.time", self.clock)
        clock.start()
        self.addCleanup(clock.stop)

    def _provider(self, **kwargs):
        from infra.external_api.tts.fish import FishAudioTTSProvider

        return FishAudioTTSProvider(api_key="f", **kwargs)

    def test_exact_model_header_and_body(self) -> None:
        # 헤더가 틀리면 Fish 가 말없이 s2.1-pro 로 합성한다. 정확한 값을 못 박는다.
        fake = FakeFish(_audio())
        with fake.patch():
            result = self._provider().synthesize("밥은 먹었냐", voice_id="fish_0123abcd")
        request = fake.sent[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), "https://api.fish.audio/v1/tts")
        self.assertEqual(request.headers["model"], "drama-3")
        self.assertEqual(request.headers["authorization"], "Bearer f")
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(json.loads(request.content), {
            "text": "밥은 먹었냐",
            "reference_id": "0123abcd",  # 앞글자 fish_ 는 떼고 보낸다
            "format": "mp3",
            "sample_rate": 44100,
            "mp3_bitrate": 128,
            "latency": "normal",
        })
        self.assertEqual(request.extensions["timeout"]["read"], 40.0)  # BE 의 45초보다 짧게
        self.assertEqual(request.extensions["timeout"]["connect"], 5.0)
        self.assertEqual((result.audio, result.content_type, result.provider, result.model),
                         (b"ID3fishmp3", "audio/mpeg", "fish", "drama-3"))

    def test_speech_route_ignores_elevenlabs_speed_and_strips_directions(self) -> None:
        _use_settings(self, elevenlabs_speed=0.7, elevenlabs_stability=0.9)
        fake = FakeFish(_audio())
        with fake.patch():
            response = TestClient(app).post("/v1/voice/speech", json={
                "text": "[감정:sad] [웃음] 그래, <whisper>보고 싶다</whisper>.",
                "voiceId": "fish_0123abcd", "speed": 1.1, "stability": 0.2, "emotion": "sad"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        body = json.loads(fake.sent[0].content)
        self.assertEqual(body["text"], "그래, 보고 싶다.")
        for key in ("prosody", "speed", "stability", "voice_settings", "emotion"):
            self.assertNotIn(key, body)

    def test_retries_once_on_429_and_503(self) -> None:
        for status in (429, 503):
            fake = FakeFish(_fish_error(status), _audio(b"ID3again"))
            with fake.patch():
                result = self._provider().synthesize("응", voice_id="fish_a1")
            self.assertEqual(len(fake.sent), 2, status)
            self.assertEqual(result.audio, b"ID3again")
            # 다시 할 때도 처음 40초 안에서 끝낸다(1초 쉰 만큼 줄어든다)
            self.assertEqual(fake.sent[1].extensions["timeout"]["read"], 39.0)
        self.assertEqual(self.clock.sleeps, [1.0, 1.0])

    def test_retry_gets_only_the_time_left(self) -> None:
        def busy_after_20s(request):
            self.clock.now += 20
            return _fish_error(429)

        fake = FakeFish(busy_after_20s, _audio(b"ID3again"))
        with fake.patch():
            result = self._provider().synthesize("응", voice_id="fish_a1")
        self.assertEqual(result.audio, b"ID3again")
        self.assertEqual(fake.sent[1].extensions["timeout"]["read"], 19.0)  # 40 - 20 - 1

    def test_slow_stream_is_cut_at_the_deadline(self) -> None:
        # 조각마다 5초씩 걸려 단계별 읽기 제한에는 안 걸려도, 전체 40초를 넘기면 끊는다
        from infra.error import VoiceProviderError

        start = self.clock.now

        def drip():
            for _ in range(20):
                self.clock.now += 5
                yield b"ID3"

        fake = FakeFish(lambda request: httpx.Response(
            200, headers={"content-type": "audio/mpeg"}, content=drip()))
        with fake.patch(), self.assertRaises(VoiceProviderError) as caught:
            self._provider().synthesize("응", voice_id="fish_a1")
        self.assertEqual(caught.exception.code, "VOICE_PROVIDER_UNAVAILABLE")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("40초 안에", str(caught.exception))
        self.assertLessEqual(self.clock.now - start, 45.0)  # BE 가 끊기 전에 답한다
        self.assertEqual(len(fake.sent), 1)  # 마감을 넘기면 다시 하지 않는다

    def test_stream_within_the_deadline_is_kept_whole(self) -> None:
        def steady():
            for part in (b"ID3", b"fish", b"mp3"):
                self.clock.now += 10
                yield part

        fake = FakeFish(lambda request: httpx.Response(
            200, headers={"content-type": "audio/mpeg"}, content=steady()))
        with fake.patch():
            result = self._provider().synthesize("응", voice_id="fish_a1")
        self.assertEqual(result.audio, b"ID3fishmp3")

    def test_error_codes(self) -> None:
        from infra.error import VoiceProviderError

        cases = [
            ([_fish_error(401)], "VOICE_PROVIDER_KEY", 1),
            ([_fish_error(403)], "VOICE_PROVIDER_KEY", 1),
            ([_fish_error(402)], "VOICE_PROVIDER_CREDITS", 1),
            ([_fish_error(429), _fish_error(429)], "VOICE_PROVIDER_RATE_LIMIT", 2),
            ([_fish_error(503), _fish_error(503)], "VOICE_PROVIDER_UNAVAILABLE", 2),
            ([_fish_error(500)], "VOICE_PROVIDER_UNAVAILABLE", 1),
            ([httpx.ReadTimeout("timed out")], "VOICE_PROVIDER_UNAVAILABLE", 1),
            ([httpx.ConnectError("refused")], "VOICE_PROVIDER_UNAVAILABLE", 1),
        ]
        for answers, code, calls in cases:
            fake = FakeFish(*answers)
            with fake.patch(), self.assertRaises(VoiceProviderError) as caught:
                self._provider().synthesize("응", voice_id="fish_a1")
            self.assertEqual(caught.exception.code, code, answers)
            self.assertEqual(caught.exception.status_code, 503)
            self.assertEqual(len(fake.sent), calls, answers)

    def test_no_retry_when_time_is_short(self) -> None:
        from infra.error import VoiceProviderError

        fake = FakeFish(_fish_error(429), _audio())
        with fake.patch(), self.assertRaises(VoiceProviderError):
            self._provider(timeout_seconds=5).synthesize("응", voice_id="fish_a1")
        self.assertEqual(len(fake.sent), 1)

    def test_empty_voice_id_is_not_sent(self) -> None:
        from infra.error import TTSProviderError

        fake = FakeFish(_audio())
        with fake.patch(), self.assertRaises(TTSProviderError):
            self._provider().synthesize("응", voice_id="fish_")
        self.assertEqual(fake.sent, [])

    def test_other_4xx_and_empty_audio_are_plain_errors(self) -> None:
        from infra.error import TTSProviderError, VoiceProviderError

        for answer in (
            httpx.Response(400, json={"status": 400, "message": "bad"}),
            httpx.Response(200, json={"status": 200, "message": "not audio"}),
            httpx.Response(200, content=b"", headers={"content-type": "audio/mpeg"}),
        ):
            fake = FakeFish(answer)
            with fake.patch(), self.assertRaises(TTSProviderError) as caught:
                self._provider().synthesize("응", voice_id="fish_a1")
            self.assertNotIsInstance(caught.exception, VoiceProviderError)
            self.assertEqual(caught.exception.code, "TTS_PROVIDER_ERROR")

    def test_route_returns_503_with_voice_code_and_no_vendor_text(self) -> None:
        _use_settings(self)
        fake = FakeFish(_fish_error(402))
        with fake.patch():
            response = TestClient(app).post(
                "/v1/voice/speech", json={"text": "응", "voiceId": "fish_a1"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "VOICE_PROVIDER_CREDITS")
        self.assertNotIn("fish says no", response.text)


class FishTextTest(unittest.TestCase):
    """drama-3 가 연기 지시로 읽는 [ ]·< >·<| |> 표시를 지운다. 말은 그대로 둔다."""

    def test_strips_directions_and_tags(self) -> None:
        from infra.external_api.tts.fish import fish_plain_text

        cases = {
            "[웃음] 그래, 잘했다.": "그래, 잘했다.",
            "[근거: m3] 밥은 먹었냐": "밥은 먹었냐",
            "<whisper>보고 싶다</whisper>": "보고 싶다",
            "<|speaker:0|>응 그래": "응 그래",
            "그래 [한숨].": "그래.",
            "<emphasis>꼭</emphasis> 와라 <soft>알겠지?</soft>": "꼭 와라 알겠지?",
            "할머니가 끓인 된장찌개, 맛있지?": "할머니가 끓인 된장찌개, 맛있지?",
            "첫 줄이다.\n둘째 줄이다.": "첫 줄이다.\n둘째 줄이다.",
            "(웃음) 그래": "(웃음) 그래",  # 괄호는 drama-3 문서에 없어 그대로 둔다
        }
        for raw, want in cases.items():
            self.assertEqual(fish_plain_text(raw), want, raw)

    def test_falls_back_when_everything_is_a_direction(self) -> None:
        from infra.external_api.tts.fish import fish_plain_text

        self.assertEqual(fish_plain_text("[웃음]"), "[웃음]")

    def test_keeps_whitelisted_cues_and_strips_the_rest(self) -> None:
        # FISH_EMOTION=cue·inline: 정해 둔 한국어 연기 표시만 남기고 나머지는 지금처럼 지운다
        from infra.external_api.tts.fish import FISH_CUES, FISH_INLINE_CUES, fish_plain_text

        cases = {  # 글: (남길 때, off 일 때)
            "[웃으며] 그래, [미소] <whisper>잘</whisper> 왔다 [잠깐 멈췄다가] 응.":
                ("[웃으며] 그래, 잘 왔다 [잠깐 멈췄다가] 응.", "그래, 잘 왔다 응."),
            "[근거: m3] [ 작게  웃으며 ] 밥은 먹었냐": ("[작게 웃으며] 밥은 먹었냐", "밥은 먹었냐"),
            # cue 방식 표시(FISH_CUES)도 남긴다
            "[반갑게 웃으며] 왔구나.": ("[반갑게 웃으며] 왔구나.", "왔구나."),
            "[울며] 보고 싶다.": ("보고 싶다.", "보고 싶다."),
        }
        for raw, (kept, off) in cases.items():
            self.assertEqual(fish_plain_text(raw), kept, raw)
            self.assertEqual(fish_plain_text(raw, keep_cues=False), off, raw)  # off: 지금처럼 다 지운다
        for cue in (*FISH_INLINE_CUES, *(c for c in FISH_CUES.values() if c)):
            self.assertEqual(fish_plain_text(f"{cue} 응."), f"{cue} 응.", cue)

    def test_cue_table(self) -> None:
        from infra.external_api.tts.fish import FISH_CUES, FISH_INLINE_CUES

        self.assertEqual(FISH_CUES, {
            "neutral": None, "calm": "[차분하게]", "warm": "[다정하게]", "happy": "[반갑게 웃으며]",
            "sad": "[조용히 서운하게]", "surprised": "[깜짝 놀라며]", "worried": "[걱정스럽게]"})
        self.assertEqual(FISH_INLINE_CUES, (
            "[웃으며]", "[작게 웃으며]", "[한숨 쉬며]", "[다정하게]", "[반갑게]", "[놀라며]", "[걱정스럽게]",
            "[차분하게]", "[작게]", "[잠깐 멈췄다가]"))


class FishEmotionSpeechTest(unittest.TestCase):
    """FISH_EMOTION: cue 면 감정 이름 → 맨 앞 연기 표시, cue·inline 이면 글 안 연기 표시를 남긴다. off 는 지금 그대로."""

    def _speak(self, text: str, emotion: str | None = None, voice: str = "fish_0123abcd", **extra) -> str:
        _use_settings(self, **extra)
        fake = FakeFish(_audio())
        body = {"text": text, "voiceId": voice, **({"emotion": emotion} if emotion else {})}
        with fake.patch():
            response = TestClient(app).post("/v1/voice/speech", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        sent = json.loads(fake.sent[0].content)
        self.assertNotIn("emotion", sent)  # drama-3 에는 감정 값을 따로 보내지 않는다
        return sent["text"]

    def test_cue_mode_prefixes_the_mapped_cue(self) -> None:
        self.assertEqual(self._speak("밥은 먹었니?", "warm", fish_emotion="cue"), "[다정하게] 밥은 먹었니?")
        self.assertEqual(self._speak("그래…", "calm", fish_emotion="cue"), "[차분하게] 그래…")
        self.assertEqual(self._speak("조심해라.", "worried", fish_emotion="cue"), "[걱정스럽게] 조심해라.")
        self.assertEqual(self._speak("응.", "neutral", fish_emotion="cue"), "응.")  # neutral 은 붙이지 않는다
        self.assertEqual(self._speak("응.", None, fish_emotion="cue"), "응.")

    def _body(self, text: str, emotion: str | None = None, **extra) -> dict:
        _use_settings(self, **extra)
        fake = FakeFish(_audio())
        with fake.patch():
            response = TestClient(app).post(
                "/v1/voice/speech", json={"text": text, "voiceId": "fish_0123abcd", "emotion": emotion}
            )
        self.assertEqual(response.status_code, 200, response.text)
        return json.loads(fake.sent[0].content)

    def test_happy_cue_is_read_calmer(self) -> None:
        # 기쁨일 때 음이 너무 높아져(대표님 블라인드 청취 2026-10-25) 표시는 그대로 두고 표현 흔들림만 낮춘다
        happy = self._body("합격했다고? 잘했다.", "happy", fish_emotion="cue")
        self.assertEqual(happy["text"], "[반갑게 웃으며] 합격했다고? 잘했다.")
        self.assertEqual(happy["temperature"], 0.5)
        self.assertNotIn("temperature", self._body("밥은 먹었니?", "warm", fish_emotion="cue"))
        self.assertNotIn("temperature", self._body("합격했다고?", "happy"))  # 감정을 끄면(off) 그대로
        self.assertEqual(self._body("잘했다.", "happy", fish_emotion="cue", fish_happy_temperature=0.4)["temperature"],
                         0.4)
        self.assertNotIn("temperature", self._body("잘했다.", "happy", fish_emotion="cue", fish_happy_temperature=0))

    def test_inline_mode_keeps_cues_and_ignores_emotion(self) -> None:
        self.assertEqual(self._speak("[웃으며] 왔구나. [미소] 밥은?", "happy", fish_emotion="inline"),
                         "[웃으며] 왔구나. 밥은?")

    def test_off_is_unchanged(self) -> None:
        self.assertEqual(self._speak("[웃으며] 왔구나.", "warm"), "왔구나.")

    def test_other_providers_never_read_fish_cues(self) -> None:
        # MiniMax 는 warm 을 모른다(None → 서버 기본값). 연기 표시는 소리 내어 읽지 않게 지운다
        _use_settings(self, fish_emotion="inline")
        with patch("infra.external_api.tts.minimax.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200, json=lambda: {"base_resp": {"status_code": 0}, "data": {"audio": "00"}})
            response = TestClient(app).post(
                "/v1/voice/speech", json={"text": "[웃으며] 왔구나.", "voiceId": "TalkTo1", "emotion": "warm"})
        self.assertEqual(response.status_code, 200, response.text)
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["text"], "왔구나.")
        self.assertNotIn("emotion", body["voice_setting"])

    def test_off_leaves_other_providers_byte_identical(self) -> None:
        # FISH_EMOTION=off 면 예전 목소리(MiniMax)에 가는 글은 예전과 글자까지 같다
        _use_settings(self)
        with patch("infra.external_api.tts.minimax.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200, json=lambda: {"base_resp": {"status_code": 0}, "data": {"audio": "00"}})
            response = TestClient(app).post(
                "/v1/voice/speech", json={"text": "[웃으며] 왔구나.", "voiceId": "TalkTo1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(mock_post.call_args.kwargs["json"]["text"], "[웃으며] 왔구나.")


class FishCloneTest(unittest.TestCase):
    """Fish 즉시 복제: 멀티파트 칸, 상태 기다리기, 실패하면 지우기, 다시 보내지 않기."""

    def setUp(self) -> None:
        sleep = patch("infra.external_api.tts.fish.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        self.sample = _temp_audio(self)

    def _provider(self):
        from infra.external_api.tts.fish import FishAudioTTSProvider

        return FishAudioTTSProvider(api_key="f")

    def test_multipart_fields_and_voice_id(self) -> None:
        fake = FakeFish(_model(model_id="abc123"), _model(model_id="def456"))
        with fake.patch():
            first = self._provider().clone_voice("TalkTo_abcd1234", self.sample)
            second = self._provider().clone_voice("TalkTo_abcd1234", self.sample)
        self.assertEqual((first, second), ("fish_abc123", "fish_def456"))
        request = fake.sent[0]
        self.assertEqual((request.method, str(request.url)), ("POST", "https://api.fish.audio/model"))
        self.assertEqual(request.headers["authorization"], "Bearer f")
        self.assertNotIn("model", request.headers)  # 합성 모델 헤더는 합성에만
        form = _form(request)
        self.assertEqual(form["type"], ["tts"])
        self.assertEqual(form["train_mode"], ["fast"])
        self.assertEqual(form["visibility"], ["private"])
        self.assertEqual(form["enhance_audio_quality"], ["true"])
        self.assertEqual(form["voices"], [self.sample.name])  # 파일 하나
        self.assertNotIn("texts", form)
        self.assertRegex(form["title"][0], r"^TalkTo_abcd1234_[0-9a-f]{6}$")
        # 다시 만들 때 옛 목소리와 제목이 겹치지 않는다
        self.assertNotEqual(form["title"], _form(fake.sent[1])["title"])
        self.sleep.assert_not_called()

    def test_waits_until_trained(self) -> None:
        fake = FakeFish(
            _model("created"),
            httpx.Response(200, json={"_id": "m1", "state": "training"}),
            httpx.Response(503, json={"status": 503, "message": "busy"}),
            httpx.Response(200, json={"_id": "m1", "state": "trained"}),
        )
        with fake.patch():
            voice_id = self._provider().clone_voice("TalkTo_x", self.sample)
        self.assertEqual(voice_id, "fish_m1")
        self.assertEqual([r.method for r in fake.sent], ["POST", "GET", "GET", "GET"])
        self.assertEqual(str(fake.sent[1].url), "https://api.fish.audio/model/m1")

    def test_failed_model_is_deleted(self) -> None:
        from infra.error import TTSProviderError, VoiceProviderError

        fake = FakeFish(_model("failed"), httpx.Response(200))
        with fake.patch(), self.assertRaises(TTSProviderError) as caught:
            self._provider().clone_voice("TalkTo_x", self.sample)
        self.assertNotIsInstance(caught.exception, VoiceProviderError)
        self.assertEqual([(r.method, str(r.url)) for r in fake.sent], [
            ("POST", "https://api.fish.audio/model"), ("DELETE", "https://api.fish.audio/model/m1")])

    def test_never_trained_is_deleted_and_unavailable(self) -> None:
        from infra.error import VoiceProviderError

        polls = [httpx.Response(200, json={"_id": "m1", "state": "created"}) for _ in range(15)]
        fake = FakeFish(_model("created"), *polls, httpx.Response(200))
        with fake.patch(), self.assertRaises(VoiceProviderError) as caught:
            self._provider().clone_voice("TalkTo_x", self.sample)
        self.assertEqual(caught.exception.code, "VOICE_PROVIDER_UNAVAILABLE")
        self.assertEqual(fake.sent[-1].method, "DELETE")
        self.assertEqual(len(fake.sent), 17)

    def test_clone_is_never_retried(self) -> None:
        from infra.error import VoiceProviderError

        for status, code in ((429, "VOICE_PROVIDER_RATE_LIMIT"), (503, "VOICE_PROVIDER_UNAVAILABLE"),
                             (402, "VOICE_PROVIDER_CREDITS"), (401, "VOICE_PROVIDER_KEY")):
            fake = FakeFish(_fish_error(status), _model())
            with fake.patch(), self.assertRaises(VoiceProviderError) as caught:
                self._provider().clone_voice("TalkTo_x", self.sample)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(len(fake.sent), 1, status)  # 다시 보내면 목소리가 두 개 생긴다

    def test_clone_log_has_voice_id_but_not_name(self) -> None:
        fake = FakeFish(_model(model_id="abc123"))
        with fake.patch(), self.assertLogs("infra.external_api.tts.fish", level="INFO") as logs:
            voice_id = self._provider().clone_voice("김할머니", self.sample)
        text = "\n".join(logs.output)
        self.assertIn(voice_id, text)
        self.assertNotIn("김할머니", text)


class CloneProviderChoiceTest(unittest.TestCase):
    """BE 가 무엇을 보내도 새 목소리는 Fish 에서 만든다(예전 회사로 가지 않는다)."""

    @patch("infra.external_api.tts.elevenlabs.httpx.post")
    @patch("infra.external_api.tts.minimax.httpx.post")
    @patch("pipeline.s4_voice.a_voice.download_audio")
    def test_clone_always_uses_fish(self, mock_download, minimax_post, elevenlabs_post) -> None:
        _use_settings(self, tts_provider="minimax")
        for provider in ("minimax", "elevenlabs", "fish", None):
            mock_download.return_value = _temp_audio(self, ".mp3")
            body = {"name": "TalkTo_abcd1234", "sampleAudioUrl": "https://storage/a.mp3"}
            if provider:
                body["provider"] = provider
            fake = FakeFish(_model(model_id="m9"))
            with fake.patch():
                response = TestClient(app).post("/v1/voice/clone", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"voiceId": "fish_m9", "provider": "fish"}, provider)
        minimax_post.assert_not_called()
        elevenlabs_post.assert_not_called()

    def test_multi_sample_clone_sends_one_built_wav(self) -> None:
        from infra.schema.voice import VoiceCloneRequest
        from infra.setting import Settings
        from pipeline.s4_voice import a_voice

        workdir = Path(tempfile.mkdtemp(prefix="voice_clone_test_"))
        sample = workdir / "sample.wav"
        sample.write_bytes(b"RIFFbuilt")
        request = VoiceCloneRequest.model_validate({
            "name": "TalkTo_abcd1234", "provider": "minimax",
            "samples": [{"audioUrl": "https://x/a.wav", "startMs": 0, "endMs": 3000}]})
        fake = FakeFish(_model(model_id="m2"))
        with fake.patch(), patch.object(a_voice, "_build_multi_sample", return_value=sample) as build:
            result = a_voice.clone_voice_from_sample(request, settings=Settings(fish_audio_api_key="f"))
        self.assertEqual((result.voice_id, result.provider), ("fish_m2", "fish"))
        build.assert_called_once()
        self.assertEqual(_form(fake.sent[0])["voices"], ["sample.wav"])
        self.assertFalse(workdir.exists())  # 올린 뒤 임시 폴더를 지운다


class VoiceSchemaContractTest(unittest.TestCase):
    """BE 가 보내는 provider:"fish" 를 받는다(모르는 값은 422). PVC 경로는 없다."""

    def test_fish_provider_is_accepted(self) -> None:
        from infra.schema.voice import VoiceCloneRequest, VoiceDeleteRequest

        clone = VoiceCloneRequest.model_validate(
            {"name": "TalkTo_x", "sampleAudioUrl": "https://a/b.wav", "provider": "fish"})
        delete = VoiceDeleteRequest.model_validate({"voiceId": "fish_m1", "provider": "fish"})
        self.assertEqual((clone.provider, delete.provider), ("fish", "fish"))
        self.assertIsNone(VoiceDeleteRequest.model_validate({"voiceId": "fish_m1"}).provider)

    def test_pvc_routes_are_gone(self) -> None:
        client = TestClient(app)
        for path in ("create", "samples", "verification", "status", "train"):
            response = client.post(f"/v1/voice/pvc/{path}", json={"voiceId": "x"})
            self.assertEqual(response.status_code, 404, path)


class VoiceDeleteTest(unittest.TestCase):
    """POST /v1/voice/delete — 목소리를 만든 회사에서 지운다(가짜 전송으로)."""

    def setUp(self) -> None:
        _use_settings(self)
        self.sent: list[httpx.Request] = []

    def _fake(self, method: str, body: dict | None = None, status: int = 200):
        """httpx.post·httpx.delete 대역. 실제 요청 모양을 MockTransport 로 만들어 모아 두고 정한 답을 준다."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.sent.append(request)
            return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

        def send(url, **kwargs):
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                return client.request(method, url, **kwargs)

        return send

    def test_minimax_provider_deletes_cloned_voice(self) -> None:
        from infra.external_api.tts.minimax import MiniMaxTTSProvider

        ok = {"base_resp": {"status_code": 0, "status_msg": "success"}}
        with patch("infra.external_api.tts.minimax.httpx.post", self._fake("POST", ok)):
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
            patch("infra.external_api.tts.minimax.httpx.post", self._fake("POST", bad)),
            self.assertRaises(TTSProviderError),
        ):
            MiniMaxTTSProvider(api_key="k").delete_voice("TalkToabc")

    def test_route_uses_minimax_by_prefix_or_when_asked(self) -> None:
        ok = {"base_resp": {"status_code": 0}}
        with patch("infra.external_api.tts.minimax.httpx.post", self._fake("POST", ok)):
            for body in ({"voiceId": "TalkToabc", "provider": "minimax"}, {"voiceId": "TalkToabc"}):
                response = TestClient(app).post("/v1/voice/delete", json=body)
                self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([json.loads(r.content)["voice_id"] for r in self.sent], ["TalkToabc"] * 2)

    def test_route_defaults_to_elevenlabs(self) -> None:
        # 예전 IVC·PVC 모두 ElevenLabs 의 같은 API 로 지운다(PVC 모듈 없이)
        with patch("infra.external_api.tts.elevenlabs.httpx.delete", self._fake("DELETE")):
            response = TestClient(app).post("/v1/voice/delete", json={"voiceId": "aBcD1234efGH5678ijKL"})
        self.assertEqual(response.status_code, 200, response.text)
        request = self.sent[0]
        self.assertEqual(request.method, "DELETE")
        self.assertEqual(str(request.url), "https://api.elevenlabs.io/v1/voices/aBcD1234efGH5678ijKL")
        self.assertEqual(request.headers["xi-api-key"], "e")
        bad = TestClient(app).post("/v1/voice/delete", json={"voiceId": "x", "provider": "other"})
        self.assertIn(bad.status_code, (400, 422))

    def test_elevenlabs_delete_error_is_raised(self) -> None:
        from infra.error import TTSProviderError
        from infra.external_api.tts.elevenlabs import ElevenLabsTTSProvider

        with (
            patch("infra.external_api.tts.elevenlabs.httpx.delete", self._fake("DELETE", {}, 400)),
            self.assertRaises(TTSProviderError),
        ):
            ElevenLabsTTSProvider(api_key="k").delete_voice("aBcD1234efGH5678ijKL")

    def test_route_picks_fish_by_prefix(self) -> None:
        # 앞글자가 먼저다: provider 를 안 보내도, 다른 값을 보내도 fish_ 는 Fish 에서 지운다
        for body in ({"voiceId": "fish_m1"}, {"voiceId": "fish_m1", "provider": "elevenlabs"},
                     {"voiceId": "m1", "provider": "fish"}):
            fake = FakeFish(httpx.Response(200))
            with fake.patch(), patch("infra.external_api.tts.elevenlabs.httpx.delete") as el_delete:
                response = TestClient(app).post("/v1/voice/delete", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            el_delete.assert_not_called()
            self.assertEqual((fake.sent[0].method, str(fake.sent[0].url)),
                             ("DELETE", "https://api.fish.audio/model/m1"), body)
            self.assertEqual(fake.sent[0].headers["authorization"], "Bearer f")

    def test_fish_delete_tolerates_missing_and_maps_errors(self) -> None:
        from infra.error import TTSProviderError, VoiceProviderError
        from infra.external_api.tts.fish import FishAudioTTSProvider

        fake = FakeFish(httpx.Response(404, json={"status": 404, "message": "gone"}))
        with fake.patch():
            FishAudioTTSProvider(api_key="f").delete_voice("fish_m1")  # 이미 없으면 넘어간다
        fake = FakeFish(_fish_error(401))
        with fake.patch(), self.assertRaises(VoiceProviderError) as caught:
            FishAudioTTSProvider(api_key="f").delete_voice("fish_m1")
        self.assertEqual(caught.exception.code, "VOICE_PROVIDER_KEY")
        self.assertEqual(len(fake.sent), 1)  # 지우기도 다시 보내지 않는다
        fake = FakeFish()
        with fake.patch(), self.assertRaises(TTSProviderError):
            FishAudioTTSProvider(api_key="f").delete_voice("fish_../model")
        self.assertEqual(fake.sent, [])


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


if __name__ == "__main__":
    unittest.main()
