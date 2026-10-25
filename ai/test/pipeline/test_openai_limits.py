"""OpenAI 호출의 시간 한도와 긴 입력 처리.

- AI-4: 녹음 분석은 분석 전용 시간 한도(300초·다시 하기 1번). 시간 초과·잘린 답·JSON 아님은 코드가 있는 오류로.
- AI-5: 임베딩은 나눠 보낸다(OpenAI 요청 하나 2,048개·약 30만 토큰 한도).
- AI-6: 대화 대답·질문 임베딩은 BE 의 45초 안에 끝나거나 포기한다.
"""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
from pydantic import SecretStr

from infra.error import AnalysisBadOutputError, AnalysisTooLongError, AppError
from infra.external_api import openai_client
from infra.schema.embedding import QueryEmbeddingRequest
from infra.schema.persona import PersonaResponseRequest
from infra.schema.transcript import TranscriptSegment
from infra.setting import Settings

SEGMENTS = [
    TranscriptSegment(segment_index=0, start_ms=0, end_ms=1000, speaker_label="SPK_0",
                      transcript_text="밥은 먹었냐"),
]


def _settings(**kw) -> Settings:
    return Settings(openai_api_key=SecretStr("sk-test"), **kw)


def _reply(content: str, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)]
    )


class ClientLimitsTest(unittest.TestCase):
    def setUp(self) -> None:
        openai_client._clients.clear()  # 목 클래스 id 가 재사용돼 예전 클라이언트가 나오지 않게
        self.addCleanup(openai_client._clients.clear)

    @patch("infra.external_api.openai_client.OpenAI")
    def test_each_purpose_gets_its_own_limits(self, cls: MagicMock) -> None:
        settings = _settings()
        openai_client.create_openai_client(settings)
        self.assertEqual(cls.call_args.kwargs["timeout"], 60)
        self.assertEqual(cls.call_args.kwargs["max_retries"], 2)
        openai_client.chat_client(settings)
        self.assertEqual(cls.call_args.kwargs["timeout"], 20)
        self.assertEqual(cls.call_args.kwargs["max_retries"], 1)
        openai_client.analysis_client(settings)
        self.assertEqual(cls.call_args.kwargs["timeout"], 300)
        self.assertEqual(cls.call_args.kwargs["max_retries"], 1)

    def test_chat_worst_case_is_under_the_backend_timeout(self) -> None:
        settings = _settings()
        worst = settings.openai_chat_timeout_seconds * (settings.openai_chat_max_retries + 1)
        self.assertLess(worst, 45)  # BE ai_server_timeout_seconds

    @patch("infra.external_api.openai_client.OpenAI")
    def test_persona_response_uses_chat_limits(self, cls: MagicMock) -> None:
        from pipeline.s3_synthesis.c_build_system_prompt import generate_persona_response

        cls.return_value.chat.completions.create.return_value = _reply("그래, 왔니.")
        request = PersonaResponseRequest.model_validate(
            {"message": "할머니", "persona": {"subjectId": "s", "instructions": "당신은 홍길동이다."}}
        )
        generate_persona_response(request, settings=_settings())
        self.assertEqual(cls.call_args.kwargs["timeout"], 20)

    @patch("infra.external_api.openai_client.OpenAI")
    def test_query_path_stays_under_the_backend_timeout(self, cls: MagicMock) -> None:
        from pipeline.s3_synthesis import d_build_memory_embedding as emb

        client = cls.return_value
        client.chat.completions.create.return_value = _reply('{"rewritten": "된장찌개 얼마나 넣어?"}')
        client.embeddings.create.return_value = SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])])
        request = QueryEmbeddingRequest.model_validate(
            {"text": "얼마나 넣어?", "history": [{"role": "user", "content": "된장찌개 끓여"}]}
        )
        emb.generate_query_embedding(request, settings=_settings(query_rewrite="on"))
        timeouts = [c.kwargs["timeout"] for c in cls.call_args_list]
        retries = [c.kwargs["max_retries"] for c in cls.call_args_list]
        self.assertEqual(sorted(timeouts), [emb.REWRITE_TIMEOUT_SECONDS, emb.QUERY_EMBED_TIMEOUT_SECONDS])
        worst = emb.REWRITE_TIMEOUT_SECONDS * 1 + emb.QUERY_EMBED_TIMEOUT_SECONDS * (emb.QUERY_EMBED_MAX_RETRIES + 1)
        self.assertLess(worst, 45)
        self.assertIn(0, retries)  # 질문 다시 쓰기는 다시 하지 않는다

    @patch("infra.external_api.openai_client.OpenAI")
    def test_voice_message_stt_stays_under_the_backend_wait(self, cls: MagicMock) -> None:
        from pipeline.s4_voice import a_voice

        cls.return_value.audio.transcriptions.create.return_value = SimpleNamespace(text=" 밥 먹었어 ")
        result = a_voice.transcribe_voice_message(
            b"RIFF", "voice.webm", language="ko", settings=_settings()
        )
        self.assertEqual(result.text, "밥 먹었어")
        self.assertEqual(cls.call_args.kwargs["timeout"], a_voice.VOICE_STT_TIMEOUT_SECONDS)
        self.assertEqual(cls.call_args.kwargs["max_retries"], a_voice.VOICE_STT_MAX_RETRIES)
        worst = a_voice.VOICE_STT_TIMEOUT_SECONDS * (a_voice.VOICE_STT_MAX_RETRIES + 1)
        self.assertLess(worst + 5, 60)  # BE ai.TRANSCRIBE_TIMEOUT(재시도 대기 몫 5초)

    def test_legacy_tts_timeouts_are_under_the_backend_timeout(self) -> None:
        from infra.external_api.tts.elevenlabs import ElevenLabsTTSProvider
        from infra.external_api.tts.minimax import MiniMaxTTSProvider

        self.assertLess(ElevenLabsTTSProvider(api_key="k").timeout_seconds, 45)
        self.assertLess(MiniMaxTTSProvider(api_key="k").timeout_seconds, 45)

    def test_health_does_not_use_the_threadpool(self) -> None:
        from infra.be_ai_api.routes.health import health

        self.assertTrue(inspect.iscoroutinefunction(health))


class AnalysisErrorsTest(unittest.TestCase):
    def setUp(self) -> None:
        openai_client._clients.clear()
        self.addCleanup(openai_client._clients.clear)

    def _run_memory(self, cls: MagicMock):
        from pipeline.s2_extraction.e_extract_memory import extract_memory_segments

        return extract_memory_segments(
            SEGMENTS, subject_context=None, subject_speaker_label=None,
            persons_result=None, sensitivity_result=None, settings=_settings(),
        )

    @patch("infra.external_api.openai_client.OpenAI")
    def test_analysis_uses_analysis_limits(self, cls: MagicMock) -> None:
        cls.return_value.chat.completions.create.return_value = _reply('{"memorySegments": []}')
        self.assertEqual(self._run_memory(cls)["memorySegments"], [])
        self.assertEqual(cls.call_args.kwargs["timeout"], 300)

    @patch("infra.external_api.openai_client.OpenAI")
    def test_timeout_becomes_coded_422(self, cls: MagicMock) -> None:
        cls.return_value.chat.completions.create.side_effect = openai.APITimeoutError(
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        )
        with self.assertRaises(AnalysisTooLongError) as caught:
            self._run_memory(cls)
        self.assertEqual((caught.exception.code, caught.exception.status_code), ("ANALYSIS_TOO_LONG", 422))

    @patch("infra.external_api.openai_client.OpenAI")
    def test_truncated_output_becomes_coded_422(self, cls: MagicMock) -> None:
        cls.return_value.chat.completions.create.return_value = _reply('{"memorySegments": [{"memo', "length")
        with self.assertRaises(AnalysisTooLongError):
            self._run_memory(cls)

    @patch("infra.external_api.openai_client.OpenAI")
    def test_malformed_output_becomes_coded_502(self, cls: MagicMock) -> None:
        cls.return_value.chat.completions.create.return_value = _reply("not json")
        with self.assertRaises(AnalysisBadOutputError) as caught:
            self._run_memory(cls)
        self.assertEqual(caught.exception.status_code, 502)

    @patch("infra.external_api.openai_client.OpenAI")
    def test_every_analysis_stage_maps_truncation(self, cls: MagicMock) -> None:
        from infra.schema.insight import ReflectionRequest
        from pipeline.s2_extraction.a_extract_persons import run_persons_analysis
        from pipeline.s2_extraction.b_extract_sensitivity import run_sensitivity_analysis
        from pipeline.s2_extraction.c_extract_linguistic_style import run_linguistic_style_analysis
        from pipeline.s2_extraction.f_extract_summary import summarize_recording
        from pipeline.s3_synthesis.b_extract_insight import run_reflection

        cls.return_value.chat.completions.create.return_value = _reply("{", "length")
        settings = _settings()
        stages = [
            lambda: run_persons_analysis(SEGMENTS, subject_context=None, subject_speaker_label=None,
                                         settings=settings),
            lambda: run_sensitivity_analysis(SEGMENTS, settings=settings),
            lambda: run_linguistic_style_analysis(SEGMENTS, subject_context=None,
                                                  subject_speaker_label=None, settings=settings),
            lambda: summarize_recording(SEGMENTS, settings=settings),
            lambda: run_reflection(
                ReflectionRequest.model_validate({"memories": [{"id": "m1", "memoryText": "기억"}]}),
                settings=settings,
            ),
        ]
        for stage in stages:
            with self.assertRaises(AnalysisTooLongError):
                stage()

    def test_analysis_error_reaches_the_backend_as_coded_json(self) -> None:
        from fastapi.testclient import TestClient

        from main import app

        client = TestClient(app, raise_server_exceptions=False)
        with patch("infra.be_ai_api.routes.analysis.run_recording_analysis",
                   side_effect=AnalysisTooLongError("memory extraction")):
            response = client.post("/v1/analysis/recording", json={
                "jobId": "00000000-0000-0000-0000-000000000001",
                "recordingId": "00000000-0000-0000-0000-000000000002",
                "transcriptSegments": [{"id": "00000000-0000-0000-0000-000000000003",
                                        "segmentIndex": 0, "startMs": 0, "endMs": 10,
                                        "transcriptText": "밥은 먹었냐"}],
            })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "ANALYSIS_TOO_LONG")


class EmbeddingBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        openai_client._clients.clear()
        self.addCleanup(openai_client._clients.clear)

    @staticmethod
    def _echo(**kwargs):
        return SimpleNamespace(data=[SimpleNamespace(embedding=[float(t[1:])]) for t in kwargs["input"]])

    @patch("infra.external_api.openai_client.OpenAI")
    def test_many_items_go_in_batches_and_keep_order(self, cls: MagicMock) -> None:
        from pipeline.s3_synthesis.d_build_memory_embedding import EMBED_BATCH_ITEMS, embed_texts

        cls.return_value.embeddings.create.side_effect = self._echo
        texts = [f"m{i}" for i in range(600)]
        vectors = embed_texts(texts, settings=_settings())
        self.assertEqual(vectors, [[float(i)] for i in range(600)])
        sizes = [len(c.kwargs["input"]) for c in cls.return_value.embeddings.create.call_args_list]
        self.assertEqual(sizes, [EMBED_BATCH_ITEMS, EMBED_BATCH_ITEMS, 600 - 2 * EMBED_BATCH_ITEMS])

    @patch("infra.external_api.openai_client.OpenAI")
    def test_long_texts_are_split_by_characters(self, cls: MagicMock) -> None:
        from pipeline.s3_synthesis.d_build_memory_embedding import EMBED_BATCH_CHARS, embed_texts

        cls.return_value.embeddings.create.side_effect = lambda **kw: SimpleNamespace(
            data=[SimpleNamespace(embedding=[1.0]) for _ in kw["input"]])
        texts = ["가" * (EMBED_BATCH_CHARS // 2)] * 5
        self.assertEqual(len(embed_texts(texts, settings=_settings())), 5)
        for c in cls.return_value.embeddings.create.call_args_list:
            self.assertLessEqual(sum(len(t) for t in c.kwargs["input"]), EMBED_BATCH_CHARS)

    @patch("infra.external_api.openai_client.OpenAI")
    def test_count_mismatch_is_an_error_not_a_silent_shift(self, cls: MagicMock) -> None:
        from pipeline.s3_synthesis.d_build_memory_embedding import embed_texts

        cls.return_value.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[1.0])])
        with self.assertRaises(AppError) as caught:
            embed_texts(["a", "b"], settings=_settings())
        self.assertEqual(caught.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
