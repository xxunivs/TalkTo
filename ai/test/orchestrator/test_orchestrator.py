"""중간계층 뼈대 테스트 — 실제 API 호출 없음."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.schema.persona import PersonaResponseRequest
from app.setting import Settings, get_settings
from main import app
from orchestrator import cli
from orchestrator.contracts import TurnInput
from orchestrator.llm import FakeLLM
from orchestrator.memory import ExportFileMemoryStore
from orchestrator.service import build_default, orchestrate_persona_response, reset_orchestrator
from pipeline.s3_synthesis.c_build_system_prompt import generate_persona_response

FIXTURE = Path(__file__).parent / "fixtures" / "turns_sample.jsonl"

REQUEST = {
    "message": "할머니 고향이 어디셨어요?",
    "history": [{"role": "user", "content": "안녕하세요"}, {"role": "assistant", "content": "어, 왔니"}],
    "memories": [
        {"id": "m1", "title": "고향", "content": "홍길동은 남원에서 태어났다.", "tags": ["category:기본정보"]},
        {"id": "m2", "content": "봄마다 고추를 심었다."},
    ],
    "persona": {"subjectId": "subj-1", "instructions": "당신은 홍길동이다."},
}


def _settings(**kw) -> Settings:
    return Settings(openai_api_key=SecretStr("sk-test"), **kw)


def _mock_openai(content: str = "남원이지.") -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=3)
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


class LegacyEquivalenceTest(unittest.TestCase):
    """v0 구성은 기존 경로와 똑같은 메시지·응답을 내야 한다(끼워 넣어도 동작이 안 바뀜)."""

    def test_same_messages_and_result(self) -> None:
        req = PersonaResponseRequest.model_validate(REQUEST)
        settings = _settings()
        with patch("app.external_api.openai_client.OpenAI") as legacy_cls:
            legacy_cls.return_value = _mock_openai()
            legacy = generate_persona_response(req, settings=settings)
            legacy_kwargs = legacy_cls.return_value.chat.completions.create.call_args.kwargs
        with patch("app.external_api.openai_client.OpenAI") as orch_cls:
            orch_cls.return_value = _mock_openai()
            orch = orchestrate_persona_response(req, settings=settings, orchestrator=build_default(settings))
            orch_kwargs = orch_cls.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(orch_kwargs["messages"], legacy_kwargs["messages"])
        self.assertEqual(orch_kwargs["model"], legacy_kwargs["model"])
        self.assertEqual(orch.model_dump(), legacy.model_dump())

    def test_no_memories_no_memory_block(self) -> None:
        req = dict(REQUEST, memories=[], history=[])
        run = build_default(_settings()).run(
            TurnInput(subject_id="s", message=req["message"], persona_instructions="p"), until="assemble")
        # 기억이 0개면 '기억이 적을 때' 절이 붙는다(LOW_MEMORY_NOTE=on 기본)
        self.assertEqual([s.name for s in run.bundle.sections], ["persona", "low_memory", "message"])
        run = build_default(_settings(low_memory_note="off")).run(
            TurnInput(subject_id="s", message=req["message"], persona_instructions="p"), until="assemble")
        self.assertEqual([s.name for s in run.bundle.sections], ["persona", "message"])


class RouteFlagTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        reset_orchestrator()

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        reset_orchestrator()

    def _post(self, flag: bool, body: dict):
        app.dependency_overrides[get_settings] = lambda: _settings(persona_orchestrator=flag)
        return self.client.post("/v1/persona/responses", json=body)

    def test_flag_off_uses_legacy(self) -> None:
        with patch("app.api.routes.persona.orchestrate_persona_response") as orch, \
                patch("app.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai()
            r = self._post(False, REQUEST)
        self.assertEqual(r.status_code, 200)
        orch.assert_not_called()
        self.assertEqual(r.json()["content"], "남원이지.")

    def test_flag_on_uses_orchestrator_and_keeps_contract(self) -> None:
        with patch("app.api.routes.persona.generate_persona_response") as legacy, \
                patch("app.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai()
            r = self._post(True, dict(REQUEST, sessionId="sess-1", speakerHint="손녀"))
        self.assertEqual(r.status_code, 200)
        legacy.assert_not_called()
        # 응답 계약 = 기존 경로와 같은 필드(emotion 은 중간계층에서 아직 고르지 않아 None)
        self.assertEqual(r.json(), {"content": "남원이지.", "retrievedMemoryIds": ["m1", "m2"],
                                    "emotion": None, "provider": "openai",
                                    "model": _settings().openai_chat_model})

    def test_fixed_prompt_skips_orchestrator_even_when_flag_on(self) -> None:
        body = dict(REQUEST, persona=dict(REQUEST["persona"], fixedPrompt=True))
        with patch("app.api.routes.persona.orchestrate_persona_response") as orch, \
                patch("app.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai()
            r = self._post(True, body)
            messages = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(r.status_code, 200)
        orch.assert_not_called()
        # 기존 경로: 지침 그대로 → 참고 기억 → 대화 → 질문. '기억에 없다' 지시는 없다.
        self.assertEqual(messages[0], {"role": "system", "content": "당신은 홍길동이다."})
        self.assertIn("남원", messages[1]["content"])
        self.assertFalse(any("코드 판정" in m["content"] for m in messages))
        self.assertEqual(r.json()["retrievedMemoryIds"], ["m1", "m2"])

    def test_new_optional_fields_accepted_by_legacy(self) -> None:
        with patch("app.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai()
            r = self._post(False, dict(REQUEST, sessionId="sess-1"))
        self.assertEqual(r.status_code, 200)


class SessionTest(unittest.TestCase):
    def test_session_accumulates_and_isolated(self) -> None:
        orch = build_default(_settings())
        orch.llm = FakeLLM("응.")
        base = dict(subject_id="s", persona_instructions="p")
        orch.run(TurnInput(message="하나", session_id="A", speaker_hint="손녀", **base))
        r2 = orch.run(TurnInput(message="둘", session_id="A", **base))
        self.assertEqual(r2.state.turn_index, 1)
        self.assertEqual([t.content for t in r2.state.recent_turns], ["하나", "응."])
        self.assertEqual(r2.state.speaker, "손녀")
        self.assertEqual(r2.next_state.turn_index, 2)
        other = orch.run(TurnInput(message="셋", session_id="B", **base))
        self.assertEqual(other.state.turn_index, 0)
        none = orch.run(TurnInput(message="넷", **base))  # 세션 id 없으면 매번 새 상태
        self.assertEqual(none.state.turn_index, 0)

    def test_given_state_is_not_saved(self) -> None:
        orch = build_default(_settings())
        orch.llm = FakeLLM()
        t = TurnInput(message="x", session_id="C", subject_id="s", persona_instructions="p")
        orch.run(t, state=orch.sessions.load(t))
        self.assertEqual(orch.sessions.load(t).turn_index, 0)


class FixtureAndStoreTest(unittest.TestCase):
    def test_cli_until_assemble_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.jsonl"
            self.assertEqual(cli.main([str(FIXTURE), "--until", "assemble", "--out", str(out)]), 0)
            rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertNotIn("generation", rows[0])
        self.assertEqual(rows[0]["bundle"]["sections"][1]["name"], "memories")
        self.assertEqual([t["stage"] for t in rows[0]["trace"]], ["session", "select", "precheck", "assemble"])

    def test_cli_mock_full_chains_session(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.jsonl"
            cli.main([str(FIXTURE), "--mock", "--out", str(out)])
            rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[1]["state"]["turn_index"], 1)  # t1 → t2 같은 세션
        self.assertEqual(rows[1]["result"]["retrieved_memory_ids"], ["m1"])

    def test_export_store_reads_neon_rows(self) -> None:
        rows = [
            {"j": json.dumps({"id": "a", "mtype": "LONG_TERM", "status": "ACTIVE", "title": "t",
                              "body": "본문", "tags": "['category:x']"}, ensure_ascii=False)},
            {"j": json.dumps({"id": "b", "mtype": "SHORT_TERM", "status": "ARCHIVED", "body": "옛것"})},
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            items = ExportFileMemoryStore(default=p).list_for_subject(
                TurnInput(subject_id="any", message="x", persona_instructions="p"))
        self.assertEqual([(m.id, m.memory_type, m.tags) for m in items], [("a", "LONG_TERM", ["category:x"])])
