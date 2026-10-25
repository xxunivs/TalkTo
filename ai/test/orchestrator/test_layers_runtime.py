"""중간계층에서 층 구성 쓰기: 명세(spec.json) 등록부, 주고받기 메시지 위치, 런타임 절, 제한 문장 로그."""
import unittest
from pathlib import Path

from orchestrator.assembler import StructuredAssembler
from orchestrator.contracts import MemoryItem, MemorySelection, PrecheckResult, RestrictedFacts, SessionState, Turn, TurnInput
from orchestrator.persona import PersonaRegistry
from orchestrator.restricted import redact_instructions

FIXTURES = Path(__file__).parent / "fixtures"
TURN = TurnInput(subject_id="subj-spec", message="할머니 뭐 해?", persona_instructions="당신은 김할매의 페르소나다.")


def assemble(*, shots_mode="system", rules="layered", speaker=None, history=(), persona_dir=FIXTURES / "personas_spec",
             memory_total=300):  # 기본은 기억 많은 페르소나(기억 적음 절 없음)
    a = StructuredAssembler(PersonaRegistry(persona_dir), rules=rules, shots_mode=shots_mode)
    st = SessionState(session_id=None, subject_id=TURN.subject_id, speaker=speaker)
    turn = TURN.model_copy(update={"history": list(history), "memory_total": memory_total})
    sel = MemorySelection(memories=[MemoryItem(id="m1", text="된장찌개를 잘 끓였다")], strategy="t")
    return a.assemble(turn, st, sel, PrecheckResult())


class SpecRegistryTest(unittest.TestCase):
    def test_spec_supplies_shots_and_relations(self) -> None:
        prof = PersonaRegistry(FIXTURES / "personas_spec").profile_for(TURN)
        self.assertIsNotNone(prof.persona_spec)
        self.assertEqual([s.user for s in prof.shots], ["밥 먹었어?", "추워요."])


class ShotsPlacementTest(unittest.TestCase):
    def test_system_mode_keeps_shots_in_head(self) -> None:
        b = assemble()
        self.assertIn("가족: 밥 먹었어?", b.sections[0].content)
        self.assertFalse(any(s.name.startswith("shot") for s in b.sections))

    def test_messages_mode_puts_pairs_after_memories(self) -> None:
        b = assemble(shots_mode="messages")
        names = [s.name for s in b.sections]
        self.assertNotIn("가족: 밥 먹었어?", b.sections[0].content)
        self.assertEqual(names[names.index("memories") + 1:names.index("memories") + 3], ["shot0u", "shot0a"])
        self.assertEqual((b.sections[names.index("shot0a")].role, b.sections[names.index("shot0a")].content),
                         ("assistant", "어, 먹었다."))
        self.assertLess(names.index("shot1a"), names.index("message"))

    def test_bad_mode_rejected(self) -> None:
        with self.assertRaises(ValueError):
            StructuredAssembler(PersonaRegistry(None), shots_mode="text")


class RuntimeSectionTest(unittest.TestCase):
    def session(self, bundle) -> str:
        return next((s.content for s in bundle.sections if s.name == "session"), "")

    def test_known_speaker_from_table(self) -> None:
        self.assertIn('이민수 (손자) — 부르는 말 "민수야"', self.session(assemble(speaker="민수")))

    def test_unknown_speaker(self) -> None:
        self.assertIn("아직 모른다", self.session(assemble()))

    def test_already_said_after_long_conversation(self) -> None:
        hist = [Turn(role="user", content="할머니"), Turn(role="assistant", content="어, 밥은 먹었니?")] * 3
        self.assertIn("이미 챙겼다", self.session(assemble(speaker="민수", history=hist)))

    def test_without_spec_only_names_speaker(self) -> None:
        b = assemble(speaker="민수", persona_dir=FIXTURES / "personas")
        self.assertEqual(self.session(b), "# 지금 상황\n- 지금 말하는 사람: 민수")

    def test_legacy_rules_keep_old_session_line(self) -> None:
        self.assertEqual(self.session(assemble(rules="legacy", speaker="민수")), "# 지금 상황\n- 지금 말하는 사람: 민수")


class RestrictedLogTest(unittest.TestCase):
    R = RestrictedFacts(replace=[{"from": "가상병으로 별세.", "to": "별세."}, {"from": "없는 문장.", "to": ""}])

    def test_partial_match_warns(self) -> None:
        with self.assertLogs("orchestrator.restricted", level="WARNING"):
            redact_instructions("2020년 가상병으로 별세.", self.R)


class ReviewFixesTest(unittest.TestCase):
    def test_spec_death_policy_used_without_intake(self) -> None:
        prof = PersonaRegistry(FIXTURES / "personas_spec").profile_for(TURN)
        self.assertEqual((prof.restricted.policy, prof.restricted.policy_source), ("ask_only", "spec.json"))
        self.assertIn("갑자기 그렇게 됐지", assemble().sections[0].content)

    def test_shots_not_added_twice_when_instructions_have_them(self) -> None:
        from pipeline.persona_prompt.render import render_layered_prompt
        spec = PersonaRegistry(FIXTURES / "personas_spec").profile_for(TURN).persona_spec
        turn = TURN.model_copy(update={"persona_instructions": render_layered_prompt(spec, include_common=False)})
        a = StructuredAssembler(PersonaRegistry(FIXTURES / "personas_spec"), rules="layered")
        head = a.assemble(turn, SessionState(session_id=None, subject_id=TURN.subject_id),
                          MemorySelection(memories=[], strategy="t"), PrecheckResult()).sections[0].content
        self.assertEqual(head.count("가족: 밥 먹었어?"), 1)

    def test_relation_word_as_speaker(self) -> None:
        s = RuntimeSectionTest.session(None, assemble(speaker="손자"))
        self.assertIn("이민수 (손자)", s)

    def test_infer_profile_reads_layered_prompts(self) -> None:
        from orchestrator.persona import infer_profile
        from pipeline.persona_prompt.render import render_layered_prompt
        from test.pipeline.test_layers import spec
        dead, alive = render_layered_prompt(spec()), render_layered_prompt(spec(basicProfile={"status": "생존"}))
        self.assertEqual((infer_profile(dead).subject_name, infer_profile(dead).deceased), ("김할매", True))
        self.assertFalse(infer_profile(alive).deceased)

    def test_generated_prompt_without_sentences_is_quiet_but_handwritten_warns(self) -> None:
        from pipeline.persona_prompt.render import render_layered_prompt
        from test.pipeline.test_layers import spec
        r = RestrictedFacts(replace=[{"from": "가상병으로 별세.", "to": "별세."}])
        with self.assertNoLogs("orchestrator.restricted", level="WARNING"):
            redact_instructions(render_layered_prompt(spec()), r)
        with self.assertLogs("orchestrator.restricted", level="WARNING"):
            redact_instructions("당신은 홍길동의 페르소나다. 손으로 고친 지시문.", r)
