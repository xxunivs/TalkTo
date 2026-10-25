import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from infra.setting import Settings, get_settings
from main import app


class EmbeddingsTest(unittest.TestCase):
    """POST /v1/embeddings — OpenAI 호출을 목킹해 항목→벡터 매핑을 검증(무API)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @patch("infra.external_api.openai_client.OpenAI")
    def test_maps_each_item_to_a_vector(self, mock_openai: MagicMock) -> None:
        resp = MagicMock()
        resp.data = [MagicMock(embedding=[0.1, 0.2]), MagicMock(embedding=[0.3, 0.4])]
        client = MagicMock()
        client.embeddings.create.return_value = resp
        mock_openai.return_value = client

        response = self.client.post(
            "/v1/embeddings",
            json={
                "jobId": "00000000-0000-0000-0000-000000000001",
                "items": [
                    {"memorySegmentId": "00000000-0000-0000-0000-000000000002",
                     "embeddingIndex": 0, "text": "첫 기억"},
                    {"memorySegmentId": "00000000-0000-0000-0000-000000000003",
                     "embeddingIndex": 1, "text": "둘째 기억"},
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        out = response.json()["embeddings"]
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["memorySegmentId"], "00000000-0000-0000-0000-000000000002")
        self.assertEqual(out[0]["embedding"], [0.1, 0.2])
        self.assertEqual(out[1]["embedding"], [0.3, 0.4])

    @patch("infra.external_api.openai_client.OpenAI")
    def test_query_embedding_needs_only_text(self, mock_openai: MagicMock) -> None:
        """기억을 찾는 질문에는 job 도 segment id 도 없다. 그걸 요구하면 422 로 튕겨
        기억 검색이 통째로 죽는다."""
        resp = MagicMock()
        resp.data = [MagicMock(embedding=[0.1, 0.2])]
        client = MagicMock()
        client.embeddings.create.return_value = resp
        mock_openai.return_value = client

        response = self.client.post(
            "/v1/embeddings/query", json={"text": "할머니 이야기 들려주세요"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["embedding"], [0.1, 0.2])


HISTORY = [{"role": "user", "content": "할머니 된장찌개 어떻게 끓여?"},
           {"role": "assistant", "content": "멸치 육수 내고 된장 풀면 되지."},
           {"role": "user", "content": "두부도 넣어?"},
           {"role": "assistant", "content": "그럼, 두부 넣지."},
           {"role": "user", "content": "호박은?"}]


def _client(chat_content: str | Exception = '{"ask": true, "rewritten": "된장찌개에 된장 얼마나 넣어?"}') -> MagicMock:
    client = MagicMock()
    emb = MagicMock()
    emb.data = [MagicMock(embedding=[0.5, 0.6])]
    client.embeddings.create.return_value = emb
    if isinstance(chat_content, Exception):
        client.chat.completions.create.side_effect = chat_content
    else:
        chat = MagicMock()
        chat.choices = [MagicMock(message=MagicMock(content=chat_content))]
        client.chat.completions.create.return_value = chat
    return client


class QueryRewriteTest(unittest.TestCase):
    """/v1/embeddings/query 의 질문 다시 쓰기(QUERY_REWRITE). 답 생성(/responses)과는 상관없다."""

    def _post(self, body: dict, client: MagicMock, **settings):
        app.dependency_overrides[get_settings] = lambda: Settings(openai_api_key=SecretStr("k"), **settings)
        self.addCleanup(app.dependency_overrides.pop, get_settings, None)
        with patch("infra.external_api.openai_client.OpenAI", return_value=client):
            return TestClient(app).post("/v1/embeddings/query", json=body)

    def test_off_by_default_response_unchanged(self) -> None:
        self.assertEqual(Settings().query_rewrite, "off")
        client = _client()
        r = self._post({"text": "얼마나 넣어?", "history": HISTORY}, client)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json()), {"provider", "model", "dimensions", "embedding"})  # 지금 응답과 같은 칸
        client.chat.completions.create.assert_not_called()
        self.assertEqual(client.embeddings.create.call_args.kwargs["input"], ["얼마나 넣어?"])

    def test_on_rewrites_with_last_four_messages(self) -> None:
        client = _client()
        r = self._post({"text": "얼마나 넣어?", "history": HISTORY}, client, query_rewrite="on")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["rewrittenQuery"], "된장찌개에 된장 얼마나 넣어?")
        self.assertEqual(client.embeddings.create.call_args.kwargs["input"], ["된장찌개에 된장 얼마나 넣어?"])
        kw = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kw["model"], "gpt-6-luna")
        prompt = kw["messages"][0]["content"]
        self.assertNotIn("어떻게 끓여", prompt)  # 앞 대화는 최근 메시지 4개만(세션 14 E6 와 같음)
        self.assertIn("대상자: 멸치 육수 내고 된장 풀면 되지.\n가족: 두부도 넣어?", prompt)
        self.assertIn("[가족의 이번 말]\n얼마나 넣어?", prompt)
        self.assertNotIn("{", prompt.split("형식:")[0])  # 자리표시자가 남지 않음

    def test_on_without_history_skips_llm(self) -> None:
        client = _client()
        r = self._post({"text": "고향이 어디야?"}, client, query_rewrite="on")
        self.assertNotIn("rewrittenQuery", r.json())
        client.chat.completions.create.assert_not_called()

    def test_on_failure_falls_back_to_original(self) -> None:
        for bad in ("다시 쓸 수 없음", '{"ask": true, "rewritten": ""}', RuntimeError("rate limit")):
            client = _client(bad)
            with self.assertLogs("pipeline.s3_synthesis.d_build_memory_embedding", "WARNING"):
                r = self._post({"text": "얼마나 넣어?", "history": HISTORY}, client, query_rewrite="on")
            self.assertEqual(r.status_code, 200)
            self.assertNotIn("rewrittenQuery", r.json())
            self.assertEqual(client.embeddings.create.call_args.kwargs["input"], ["얼마나 넣어?"])


if __name__ == "__main__":
    unittest.main()
