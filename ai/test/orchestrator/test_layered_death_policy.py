"""4층으로 만든 저장 지시문을 structured+layered 로 조립할 때 사망 정책이 두 벌 들어가지 않는가(설계 문서 1-4)."""
import unittest

from pydantic import SecretStr

from infra.schema.persona import PersonaAssemblyRequest
from infra.schema.subject import IntakeContext, SubjectContext
from infra.setting import Settings
from orchestrator.assembler import StructuredAssembler
from orchestrator.contracts import MemorySelection, PrecheckResult, SessionState, TurnInput
from orchestrator.persona import PersonaRegistry
from pipeline.s3_synthesis.c_build_system_prompt import assemble_persona_instructions
from test.pipeline.test_persona_prompt import INTAKE, SUBJECT  # 가상 인물(김할매), 설문 M9-01 사망 = 물으면만

DEATH_HEAD = "**사망·임종 화제는 자해와 다르다.**"
ASK_ONLY = "물으면 피하지 말고"  # ask_only 변형에만 있는 말


def _head(instructions: str, rules: str = "layered") -> str:
    a = StructuredAssembler(PersonaRegistry(None), rules=rules, shots_mode="messages")  # 등록부 없음(운영과 같음)
    turn = TurnInput(subject_id="subj-none", message="할머니 어떻게 돌아가셨어?", persona_instructions=instructions)
    b = a.assemble(turn, SessionState(session_id=None, subject_id="subj-none"),
                   MemorySelection(memories=[], strategy="t"), PrecheckResult())
    return b.sections[0].content


class LayeredDeathPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        req = PersonaAssemblyRequest(subject_context=SubjectContext(**SUBJECT), intake_context=IntakeContext(**INTAKE))
        self.stored = assemble_persona_instructions(req, Settings(openai_api_key=SecretStr("k"))).instructions

    def test_stored_layered_instructions_have_one_death_rule(self) -> None:
        self.assertEqual(self.stored.count(DEATH_HEAD), 1)
        self.assertIn(ASK_ONLY, self.stored)
        head = _head(self.stored)
        self.assertEqual(head.count(DEATH_HEAD), 1)  # 고치기 전에는 2(앞 L1 deny + 저장 L1 ask_only)
        self.assertIn(ASK_ONLY, head)  # 남는 것은 만들 때 정한 정책
        self.assertEqual(head.count("# 기억을 쓰는 법"), 1)  # L1 전체가 한 벌

    def test_handwritten_instructions_still_get_layered_rules(self) -> None:
        hand = "당신은 김가상의 페르소나를 가진 챗봇이다. 사후 페르소나다.\n\n# 말투\n- 짧게 말한다."
        head = _head(hand)
        self.assertTrue(head.startswith("# 공통 규칙 (모든 페르소나 공통, prompts/rules)\n"))
        self.assertEqual(head.count(DEATH_HEAD), 1)

    def test_legacy_rules_unchanged(self) -> None:
        head = _head(self.stored, rules="legacy")
        self.assertTrue(head.startswith("# 공통 규칙 (모든 페르소나 공통, shared_rules.md)\n"))


if __name__ == "__main__":
    unittest.main()
