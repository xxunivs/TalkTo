"""공통 규칙 + 페르소나 조립기(structured_v1)."""
import unittest
from pathlib import Path

from pydantic import SecretStr

from infra.setting import Settings
from orchestrator.assembler import StructuredAssembler, dedupe_rules
from orchestrator.contracts import MemoryItem, MemorySelection, PrecheckResult, SessionState, Turn, TurnInput
from orchestrator.persona import PersonaRegistry, infer_profile
from orchestrator.service import build_default
from pipeline.s3_synthesis.c_build_system_prompt import fixed_rules
from pipeline.s3_synthesis.common_rules import common_rule_lines

PERSONAS = Path(__file__).parent / "fixtures" / "personas"

PRIVATE = ("당신은 홍길동의 페르소나를 가진 챗봇이다.\n\n# 기본 정보\n홍길동. 2020년 별세.\n\n"
           "## 가족 3명\n- 철수 (아들)\n")
PUBLIC = ("당신은 김공개 본인의 말투로 말하는 페르소나다.\n\n# 방문자 (누가 올지 모른다)\n- 누가 올지 모른다.\n\n"
          + fixed_rules(public=True, deceased=False, has_family=False) + "\n\n# 기억\n(런타임 주입)")


def _assemble(turn, *, persona_dir=None, state=None, memories=(), directive=None):
    a = StructuredAssembler(PersonaRegistry(persona_dir))
    st = state or SessionState(session_id=None, subject_id=turn.subject_id)
    return a.assemble(turn, st, MemorySelection(memories=list(memories), strategy="t"),
                      PrecheckResult(directive=directive))


class ProfileTest(unittest.TestCase):
    def test_infer(self) -> None:
        p = infer_profile(PRIVATE)
        self.assertEqual((p.subject_name, p.public, p.deceased, p.has_family), ("홍길동", False, True, True))
        q = infer_profile(PUBLIC)
        self.assertEqual((q.subject_name, q.public, q.deceased, q.has_family), ("김공개", True, False, False))

    def test_registry_by_id_and_name(self) -> None:
        reg = PersonaRegistry(PERSONAS)
        by_id = reg.profile_for(TurnInput(subject_id="subj-demo", message="x", persona_instructions="아무거나"))
        self.assertEqual((by_id.source, by_id.self_label, len(by_id.shots)), ("registry", "할머니", 2))
        by_name = reg.profile_for(TurnInput(subject_id="uuid-1", message="x", persona_instructions=PRIVATE))
        self.assertEqual(by_name.source, "registry")
        other = reg.profile_for(TurnInput(subject_id="uuid-2", message="x", persona_instructions=PUBLIC))
        self.assertEqual(other.source, "inferred")


class StructuredAssemblerTest(unittest.TestCase):
    def test_private_matches_L1_split_condition(self) -> None:
        # 등록 없는 손글씨 프롬프트 → 첫 system 은 공통 규칙 + 손글씨 원문 그대로
        b = _assemble(TurnInput(subject_id="s", message="안녕", persona_instructions=PRIVATE))
        expected = ("# 공통 규칙 (모든 페르소나 공통, shared_rules.md)\n"
                    + fixed_rules(public=False, deceased=True, has_family=True) + "\n\n# 페르소나\n" + PRIVATE)
        self.assertEqual(b.sections[0].content, expected)
        self.assertEqual([s.name for s in b.sections], ["rules+persona", "message"])

    def test_public_dedupes_rules_and_empty_headings(self) -> None:
        b = _assemble(TurnInput(subject_id="s", message="안녕", persona_instructions=PUBLIC))
        head = b.sections[0].content
        rule = "- **처음 보는 사람인 것이 정상이다 (중요)."
        self.assertEqual(head.count(rule), 1)  # 공통 규칙에만 한 번
        persona_part = head.split("# 페르소나\n", 1)[1]
        self.assertNotIn("# 안전 가드레일", persona_part)
        self.assertIn("# 기억", persona_part)

    def test_dedupe_keeps_original_when_nothing_removed(self) -> None:
        text = "당신은 x다.\n# 말투\n- 짧게\n"
        self.assertEqual(dedupe_rules(text, common_rule_lines(public=False, deceased=True, has_family=True)), text)

    def test_order_with_package_memories_delta_directive_session(self) -> None:
        state = SessionState(session_id="A", subject_id="subj-demo", speaker="손녀",
                             recent_turns=[Turn(role="user", content="전에"), Turn(role="assistant", content="응")])
        b = _assemble(TurnInput(subject_id="subj-demo", message="지금", persona_instructions=PRIVATE),
                      persona_dir=PERSONAS, state=state,
                      memories=[MemoryItem(id="m1", text="남원 출생")], directive="모른다고 해라")
        self.assertEqual([s.name for s in b.sections],
                         ["rules+persona", "memories", "directive", "session", "history0", "history1", "message"])
        head = b.sections[0].content
        self.assertIn("# 말투 카드 (예시)", head)
        self.assertIn("가족: 밥 먹었어?\n할머니: 어, 먹었다.", head)
        self.assertNotIn("안 쓰임", head)  # max_shots=2
        self.assertIn("손녀", b.sections[3].content)

    def test_request_history_wins_over_session(self) -> None:
        state = SessionState(session_id="A", subject_id="s", recent_turns=[Turn(role="user", content="세션")])
        b = _assemble(TurnInput(subject_id="s", message="m", persona_instructions=PRIVATE,
                                history=[Turn(role="user", content="BE")]), state=state)
        self.assertEqual([s.content for s in b.sections if s.name.startswith("history")], ["BE"])

    def test_setting_selects_assembler(self) -> None:
        s = Settings(openai_api_key=SecretStr("k"), orchestrator_assembler="structured")
        self.assertEqual(build_default(s).assembler.impl, "structured_v1")
        self.assertEqual(build_default(Settings(openai_api_key=SecretStr("k"))).assembler.impl, "legacy_v0")
