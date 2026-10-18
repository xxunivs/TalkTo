"""위기·죽음을 묻는 말(ai-correct-1), 빈 대답(ai-correct-4), BE 의 고인 여부(ai-correct-10). 가짜 데이터만."""

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from infra.error import EmptyReplyError
from infra.schema.persona import PersonaResponseRequest
from infra.setting import Settings, get_settings
from main import app
from orchestrator.contracts import Generation, MemoryItem, MemorySelection, SessionState, TurnInput
from orchestrator.guard import ASK_BACK, GuardV1, GuardV11
from orchestrator.llm import FakeLLM
from orchestrator.persona import PersonaRegistry
from orchestrator.precheck import CoveragePrecheck, CoveragePrecheckV11, is_safety_turn
from orchestrator.service import build_default

PERSONA = "당신은 홍길동의 페르소나를 가진 챗봇이다.\n\n# 기본 정보\n홍길동. 남원 출신.\n"
MEMS = [MemoryItem(id="a", title="된장찌개", text="된장찌개는 멸치 육수에 두부를 넣고 끓였다.")]
SEL = MemorySelection(memories=MEMS, strategy="t")
CRISIS = "할머니 나 너무 힘들어서 죽고 싶어요"
DEATH = "할머니 어떻게 돌아가셨어요?"
CRISIS_REPLY = "아이고, 우리 강아지 많이 힘들었구나. 혼자 견디지 말고 109에 전화해서 이야기해 보자. 할머니는 네 편이야."


def turn(msg: str, **kw) -> TurnInput:
    return TurnInput(subject_id="s", message=msg, persona_instructions=PERSONA, be_memories=MEMS, **kw)


def state() -> SessionState:
    return SessionState(session_id="x", subject_id="s")


class SafetyBypassTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = PersonaRegistry(None)
        cls.guards = [GuardV1(cls.registry), GuardV11(cls.registry)]

    def test_detects_crisis_and_death_questions_only(self) -> None:
        for text in (CRISIS, DEATH, "사라지고 싶어요", "자살하고 싶어", "임종 때 뭐라고 하셨어요?"):
            self.assertTrue(is_safety_turn(text), text)
        for text in ("된장찌개 어떻게 끓였어요?", "고향이 어디예요?", "보고 싶어요"):
            self.assertFalse(is_safety_turn(text), text)

    def test_precheck_adds_no_dont_know_directive(self) -> None:
        for precheck in (CoveragePrecheck(self.registry), CoveragePrecheckV11(self.registry)):
            for msg in (CRISIS, DEATH):
                result = precheck.check(turn(msg), state(), SEL)
                self.assertIsNone(result.directive, (type(precheck).__name__, msg))

    def test_guard_keeps_the_crisis_reply(self) -> None:
        for guard in self.guards:
            for answer in ("[근거: 없음]\n" + CRISIS_REPLY, CRISIS_REPLY):
                result = guard.apply(Generation(text=answer, provider="t", model="t"), turn(CRISIS), state(), SEL)
                self.assertNotEqual(result.action, "ask_back", (guard.impl, answer[:10]))
                self.assertIn("109", result.text)
                self.assertNotIn("[근거", result.text)  # 헤더는 여전히 뗀다

    def test_guard_does_not_ask_the_family_how_the_persona_died(self) -> None:
        answer = "[근거: 없음]\n그 얘기는 마음 아프니 그만하자. 할머니는 지금 편안해."
        for guard in self.guards:
            result = guard.apply(Generation(text=answer, provider="t", model="t"), turn(DEATH), state(), SEL)
            self.assertNotEqual(result.action, "ask_back", guard.impl)
            self.assertNotIn(ASK_BACK["family"][0], result.text)

    def test_ordinary_questions_are_still_checked(self) -> None:
        # 위기·죽음이 아닌 질문은 예전처럼 근거 없음 → 되묻기
        for guard in self.guards:
            result = guard.apply(Generation(text="[근거: 없음]\n된장은 3년 묵혔지.", provider="t", model="t"),
                                 turn("된장찌개에 된장 몇 년 묵힌 거 넣었어요?"), state(), SEL)
            self.assertEqual(result.action, "ask_back", guard.impl)


class DeceasedFromBackendTest(unittest.TestCase):
    """등록부가 없으면 지시문 글자로 고인 여부를 추정했다. BE 가 보낸 값이 있으면 그것을 쓴다."""

    LIVING = "당신은 박영자의 페르소나를 가진 챗봇이다. 돌아가신 할아버지 이야기를 자주 한다."
    DECEASED = "당신은 김순자의 페르소나를 가진 챗봇이다. 밥 먹었냐고 자주 묻는다."

    def _profile(self, instructions: str, deceased):
        t = TurnInput(subject_id="s", message="안녕", persona_instructions=instructions, deceased=deceased)
        return PersonaRegistry(None).profile_for(t)

    def test_backend_value_wins_over_word_guessing(self) -> None:
        self.assertFalse(self._profile(self.LIVING, False).deceased)
        self.assertTrue(self._profile(self.DECEASED, True).deceased)

    def test_unknown_keeps_the_old_guess(self) -> None:
        self.assertTrue(self._profile(self.LIVING, None).deceased)
        self.assertFalse(self._profile(self.DECEASED, None).deceased)


# 예전에 빈 대답이 저장된 대화(앞 대화에 빈 assistant 줄)
EMPTY_TURN_REQUEST = {
    "message": "할머니 뭐 해요?",
    "history": [{"role": "user", "content": "안녕하세요"}, {"role": "assistant", "content": ""}],
    "persona": {"subjectId": "subj-1", "instructions": "당신은 홍길동이다."},
}


class EmptyReplyTest(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings(openai_api_key=SecretStr("sk-test"))

    def test_orchestrator_raises_and_does_not_save_the_turn(self) -> None:
        orch = build_default(self._settings())
        orch.llm = FakeLLM("   ")
        t = TurnInput(subject_id="s", message="안녕", persona_instructions=PERSONA, session_id="sess")
        with self.assertRaises(EmptyReplyError):
            orch.run(t)
        self.assertNotIn("sess", orch.sessions._data)  # 실패한 턴은 세션에 남기지 않는다

    @patch("infra.external_api.openai_client.OpenAI")
    def test_api_returns_502_and_skips_empty_history(self, cls: MagicMock) -> None:
        from infra.external_api import openai_client

        openai_client._clients.clear()
        self.addCleanup(openai_client._clients.clear)
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content=None), finish_reason="content_filter")]
        cls.return_value.chat.completions.create.return_value = resp
        settings = self._settings()
        app.dependency_overrides[get_settings] = lambda: settings
        self.addCleanup(app.dependency_overrides.pop, get_settings, None)
        client = TestClient(app)

        response = client.post("/v1/persona/responses", json=EMPTY_TURN_REQUEST)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "EMPTY_REPLY")
        # 예전에 저장된 빈 대답 줄은 빼고 보낸다(422 로 막히지 않는다)
        messages = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
        self.assertNotIn("", [m["content"] for m in messages])

        resp.choices = [MagicMock(message=MagicMock(content="응, 밥 먹었다."), finish_reason="stop")]
        response = client.post("/v1/persona/responses", json=EMPTY_TURN_REQUEST)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "응, 밥 먹었다.")

    def test_request_schema_drops_blank_turns(self) -> None:
        request = PersonaResponseRequest.model_validate(EMPTY_TURN_REQUEST)
        self.assertEqual([m.content for m in request.history], ["안녕하세요"])


if __name__ == "__main__":
    unittest.main()
