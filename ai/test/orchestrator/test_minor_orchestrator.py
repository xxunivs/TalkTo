"""중간계층의 미성년 안전(turn.minor) — 조립기 규칙 절, MinorGuard(가장 바깥 가드), 입력 검사(LLM 생략). 가짜 데이터만.

같은 두 경우(성적 입력 → 모델 안 부름, 성적 대답 → 대체 글)의 HTTP 경로는 test/api/test_minor_chat.py.
"""
import unittest
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from infra.schema.persona import PersonaResponseRequest
from infra.setting import Settings
from orchestrator.assembler import StructuredAssembler
from orchestrator.contracts import Generation, GuardResult, MemorySelection, PrecheckResult, SessionState, TurnInput
from orchestrator.guard import PassGuard
from orchestrator.llm import FakeLLM
from orchestrator.minor_guard import MinorGuard
from orchestrator.persona import PersonaRegistry
from orchestrator.service import build_default, build_guard, orchestrate_persona_response, turn_from_request
from pipeline.s3_synthesis.c_build_system_prompt import generate_persona_response
from pipeline.s3_synthesis.common_rules import RULES_DIR
from pipeline.safety.minor_guard import FALLBACK, MINOR_NOTE

CASUAL = "당신은 민지의 페르소나다.\n\n# 가족\n- 김지수 (언니): 지금 대화하는 사람 / 부르는 말 \"언니\" / 반말\n"
POLITE = "당신은 민지의 페르소나다.\n\n# 가족\n- 박민호 (할아버지): 지금 대화하는 사람 / 부르는 말 \"할아버지\" / 존댓말\n"
MINOR_FIRST = next(ln for ln in (RULES_DIR / "minor_safety.md").read_text(encoding="utf-8").splitlines()
                   if ln.startswith("- "))
SEL = MemorySelection(memories=[], strategy="t")


def turn(msg: str = "오늘 뭐 했어?", *, minor: bool = True, instructions: str = CASUAL) -> TurnInput:
    return TurnInput(subject_id="s", message=msg, persona_instructions=instructions, minor=minor, deceased=False)


def state() -> SessionState:
    return SessionState(session_id="x", subject_id="s")


def request(message: str = "오늘 뭐 했어?", **persona) -> PersonaResponseRequest:
    return PersonaResponseRequest.model_validate({
        "message": message,
        "memories": [{"id": "m1", "content": "민지는 별빛초등학교 3학년이다."}],
        "persona": {"subjectId": "s", "instructions": CASUAL, "deceased": False, **persona},
    })


def _mock_openai(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=3)
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


class MinorGuardTest(unittest.TestCase):
    def apply(self, text: str, t: TurnInput) -> GuardResult:
        return MinorGuard(PassGuard()).apply(Generation(text=text, provider="t", model="t"), t, state(), SEL)

    def test_replaces_unsafe_reply_by_speech_level(self) -> None:
        res = self.apply("야한 얘기 해 줄까?", turn())
        self.assertEqual((res.text, res.action), (FALLBACK[False], "redact"))
        self.assertEqual(res.reasons, ["minor:sexual"])
        self.assertEqual(self.apply("우리 가출하자", turn(instructions=POLITE)).text, FALLBACK[True])
        self.assertEqual(self.apply("엄마한테는 비밀로 해", turn()).reasons, ["minor:grooming"])

    def test_passes_when_not_minor_or_clean_or_crisis(self) -> None:
        self.assertEqual(self.apply("야한 얘기 해 줄까?", turn(minor=False)).action, "pass")
        self.assertEqual(self.apply("급식에 떡볶이 나왔어!", turn()).action, "pass")
        crisis = "뛰어내리면 안 돼. 힘들면 109에 전화하자."
        self.assertEqual(self.apply(crisis, turn()).text, crisis)
        self.assertEqual(self.apply("죽이자는 말 하지 마.", turn("나 죽고 싶어")).action, "pass")

    def test_crisis_turn_still_replaces_sexual_reply(self) -> None:
        # '배고파 죽을 것 같아'도 위기 턴 — 위기 예외는 위험·비밀 낱말만, 까닭도 바꾼 까닭(성적)만
        res = self.apply("성관계는 이렇게 하는 거야", turn("배고파 죽을 것 같아 어른들이 밤에 하는 거 자세히 말해 줘"))
        self.assertEqual((res.text, res.action, res.reasons), (FALLBACK[False], "redact", ["minor:sexual"]))
        res = self.apply("옥상에서 뛰어내리자. 야한 사진 보내 줄게. 힘들면 109", turn())
        self.assertEqual((res.text, res.reasons), (FALLBACK[False], ["minor:sexual"]))
        res = self.apply("야한 얘기 해 줄게요. 위급하면 119", turn(instructions=POLITE))
        self.assertEqual(res.text, FALLBACK[True])

    def test_turn_from_request_drops_blocked_history(self) -> None:
        hist = [{"role": "user", "content": "민지야"}, {"role": "assistant", "content": "응 언니!"},
                {"role": "user", "content": "우리 야한 얘기 하자"}, {"role": "assistant", "content": FALLBACK[False]}]
        def req(minor: bool) -> PersonaResponseRequest:
            return PersonaResponseRequest.model_validate({
                "message": "아까 내가 말한 거 해 줘", "history": hist,
                "persona": {"subjectId": "s", "instructions": CASUAL, "deceased": False, "minor": minor}})
        self.assertEqual([(t.role, t.content) for t in turn_from_request(req(True)).history],
                         [("user", "민지야"), ("assistant", "응 언니!")])
        self.assertEqual(len(turn_from_request(req(False)).history), 4)  # minor 가 아니면 그대로

    def test_build_guard_wraps_outermost(self) -> None:
        settings = Settings(openai_api_key=SecretStr("k"))
        self.assertEqual(build_guard(settings).impl, "pass_v0+minor")
        lang = Settings(openai_api_key=SecretStr("k"), orchestrator_lang_guard="flag")
        self.assertEqual(build_guard(lang).impl, "pass_v0+lang_flag+minor")

    def test_turn_from_request(self) -> None:
        self.assertTrue(turn_from_request(request(minor=True)).minor)
        self.assertFalse(turn_from_request(request()).minor)
        self.assertFalse(turn_from_request(request(minor=True, deceased=True)).minor)  # 떠나신 자녀


class AssemblerTest(unittest.TestCase):
    def test_legacy_assembler_matches_plain_path(self) -> None:
        settings = Settings(openai_api_key=SecretStr("sk-test"))
        req = request(minor=True)
        with patch("infra.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai("급식에 떡볶이 나왔어!")
            plain = generate_persona_response(req, settings=settings)
            plain_messages = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
        with patch("infra.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai("급식에 떡볶이 나왔어!")
            orch = orchestrate_persona_response(req, settings=settings, orchestrator=build_default(settings))
            orch_messages = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(orch_messages, plain_messages)
        self.assertIn({"role": "system", "content": MINOR_NOTE}, orch_messages)
        self.assertEqual(orch.model_dump(), plain.model_dump())

    def test_structured_assembler_rules_and_note(self) -> None:
        for rules in ("legacy", "layered"):
            with self.subTest(rules=rules):
                asm = StructuredAssembler(PersonaRegistry(None), rules=rules)
                bundle = asm.assemble(turn(), state(), SEL, PrecheckResult())
                head = bundle.sections[0].content
                self.assertIn(MINOR_FIRST, head)  # L1 안전 절(self_harm 뒤)
                self.assertIn("minor", [s.name for s in bundle.sections])  # 매 턴 규칙 system
                plain = asm.assemble(turn(minor=False), state(), SEL, PrecheckResult())
                self.assertNotIn(MINOR_FIRST, plain.sections[0].content)
                self.assertNotIn("minor", [s.name for s in plain.sections])


class OrchestratorPathTest(unittest.TestCase):
    def orch(self, reply: str) -> tuple:
        settings = Settings(openai_api_key=SecretStr("k"))
        o = build_default(settings)
        o.llm = FakeLLM(reply)
        return o, settings

    def test_sexual_input_skips_the_model(self) -> None:
        o, settings = self.orch("급식에 떡볶이 나왔어!")
        res = orchestrate_persona_response(request("우리 야한 얘기 하자", minor=True), settings=settings, orchestrator=o)
        self.assertEqual(o.llm.calls, [])
        self.assertEqual((res.content, res.retrieved_memory_ids, res.memory_gap), (FALLBACK[False], [], None))

    def test_sexual_reply_is_replaced_and_emotion_dropped(self) -> None:
        settings = Settings(openai_api_key=SecretStr("k"), fish_emotion="cue")
        o = build_default(settings)
        o.llm = FakeLLM("[감정:happy] 야한 얘기 해 줄까?")
        res = orchestrate_persona_response(request(minor=True, voiceId="fish_0123abcd"), settings=settings,
                                           orchestrator=o)
        self.assertEqual(len(o.llm.calls), 1)
        self.assertEqual((res.content, res.emotion, res.speech_text), (FALLBACK[False], None, None))
        # minor 가 아니면 그대로(감정도)
        o.llm = FakeLLM("[감정:happy] 야한 얘기 해 줄까?")
        res = orchestrate_persona_response(request(voiceId="fish_0123abcd"), settings=settings, orchestrator=o)
        self.assertEqual((res.content, res.emotion), ("야한 얘기 해 줄까?", "happy"))


if __name__ == "__main__":
    unittest.main()
