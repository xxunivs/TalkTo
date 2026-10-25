"""사전 판정(화제어 커버율). kiwipiepy 가 없으면 건너뜀."""
import importlib.util
import unittest

from pydantic import SecretStr

from infra.setting import Settings
from orchestrator.contracts import MemoryItem, MemorySelection, SessionState, TurnInput
from orchestrator.precheck import is_question, profile_section, strip_preamble

HAS_KIWI = importlib.util.find_spec("kiwipiepy") is not None

PERSONA = ("당신은 박영자의 페르소나를 가진 챗봇이다.\n\n# 할머니 기본 정보\n\n박영자. 남원 출신.\n\n"
           "## 가족\n- 도윤 (손자)\n")
MEMS = [
    MemoryItem(id="m1", title="된장찌개", text="된장찌개에는 간장을 조금 넣고 두부를 넣는다."),
    MemoryItem(id="m2", title="노인정", text="박영자는 동네 노인정에 수십 년 다녔다."),
    MemoryItem(id="m3", title="출생", text="박영자는 1940년 남원에서 태어났다."),
]


def _check(msg, memories=MEMS, persona=PERSONA, speaker="도윤"):
    from orchestrator.precheck import CoveragePrecheck
    turn = TurnInput(subject_id="s", message=msg, persona_instructions=persona, speaker_hint=speaker)
    return CoveragePrecheck().check(turn, SessionState(session_id=None, subject_id="s", speaker=speaker),
                                    MemorySelection(memories=memories, strategy="t"))


class TextRulesTest(unittest.TestCase):
    def test_question_detection(self) -> None:
        for q in ["고향이 어디야?", "된장찌개에 간장 넣어?", "노인정 다녔지", "언제 결혼했어", "기억나"]:
            self.assertTrue(is_question(q), q)
        for s in ["오늘 좀 힘들었어.", "나 이사했어", "상하이에서 전화했어."]:
            self.assertFalse(is_question(s), s)

    def test_strip_preamble(self) -> None:
        self.assertEqual(strip_preamble("할머니 나 도윤이야. 고향이 어디야?"), "고향이 어디야?")
        self.assertEqual(strip_preamble("엄마, 나 정우야. 내가 누구게?"), "내가 누구게?")
        self.assertEqual(strip_preamble("나 누군지 알지?"), "나 누군지 알지?")

    def test_profile_section(self) -> None:
        self.assertIn("남원 출신", profile_section(PERSONA))
        self.assertNotIn("도윤", profile_section(PERSONA))


@unittest.skipUnless(HAS_KIWI, "kiwipiepy not installed")
class CoveragePrecheckTest(unittest.TestCase):
    def test_fact_known(self) -> None:
        r = _check("할머니 나 도윤이야. 된장찌개에 간장 넣어?")
        self.assertTrue(r.known)
        self.assertIsNone(r.directive)

    def test_synonym_fixes_hometown(self) -> None:  # 동의어 없으면 '고향' 질문이 오차단된다
        self.assertTrue(_check("할머니 고향이 어디야?").known)
        from orchestrator.precheck import CoveragePrecheck
        turn = TurnInput(subject_id="s", message="할머니 고향이 어디야?", persona_instructions="당신은 박영자의 페르소나를 가진 챗봇이다.")
        no_syn = CoveragePrecheck(synonyms={}).check(turn, SessionState(session_id=None, subject_id="s"),
                                                      MemorySelection(memories=MEMS, strategy="t"))
        self.assertFalse(no_syn.known)

    def test_absent_gets_directive(self) -> None:
        r = _check("할머니 나 도윤이야. 우리 예전에 제주도 같이 갔던 거 기억나?")
        self.assertFalse(r.known)
        self.assertIn("기억이 잘 안 나", r.directive)
        self.assertIn("제주도", r.directive)

    def test_statements_and_greetings_not_judged(self) -> None:
        self.assertIsNone(_check("할머니 나 도윤이야. 나 이번에 이사했어.").known)
        self.assertIsNone(_check("할머니 나 도윤이야. 오늘 좀 힘들었어.").known)
        self.assertIsNone(_check("엄마 나 정우야. 내가 누구게?").known)

    def test_public_directive_is_polite(self) -> None:
        from orchestrator.persona import PersonaRegistry
        from orchestrator.precheck import CoveragePrecheck
        public = "당신은 김공개 본인의 말투로 말하는 페르소나다.\n# 방문자\n"
        turn = TurnInput(subject_id="s", message="제주도 가 보셨어요?", persona_instructions=public)
        r = CoveragePrecheck(PersonaRegistry(None)).check(turn, SessionState(session_id=None, subject_id="s"),
                                                          MemorySelection(memories=MEMS, strategy="t"))
        self.assertIn("제가 잘 모르겠네요", r.directive)

    def test_wired_by_setting_and_reaches_prompt(self) -> None:
        from orchestrator.llm import FakeLLM
        from orchestrator.service import build_default
        s = Settings(openai_api_key=SecretStr("k"), orchestrator_precheck="coverage",
                     orchestrator_assembler="structured")
        orch = build_default(s)
        orch.llm = FakeLLM()
        run = orch.run(TurnInput(subject_id="s", message="제주도 갔던 거 기억나?", persona_instructions=PERSONA,
                                 be_memories=MEMS, memory_total=300))  # 기억 많은 페르소나(적으면 판정 안 함)
        self.assertEqual(orch.precheck.impl, "coverage_v1")
        self.assertIn("directive", [sec.name for sec in run.bundle.sections])
        self.assertIn("코드 판정", orch.llm.calls[0][2]["content"])


# ---- v1.1
def test_v11_merges_split_names_and_drops_light_words():
    from orchestrator.precheck import CoveragePrecheckV11
    p = CoveragePrecheckV11()
    assert p.topic_terms("정우는 어느 대학 나왔지?", set()) == ["나오", "대학", "정우"]
    assert "엄마" not in p.topic_terms("엄마가 맨날 하던 잔소리 있잖아, 뭐였지?", set())


def test_v11_matches_full_name_and_synonym_by_substring():
    from orchestrator.contracts import MemoryItem, MemorySelection, SessionState, TurnInput
    from orchestrator.precheck import CoveragePrecheckV11
    sel = MemorySelection(memories=[MemoryItem(id="m1", text="이정우는 한빛대 경제학과를 졸업했다.")], strategy="t")
    turn = TurnInput(subject_id="s", message="정우는 어느 대학 나왔지?", persona_instructions="")
    r = CoveragePrecheckV11().check(turn, SessionState(session_id=None, subject_id="s"), sel)
    assert r.known is True
    turn2 = TurnInput(subject_id="s", message="강아지 키운 적 있어?", persona_instructions="")
    assert CoveragePrecheckV11().check(turn2, SessionState(session_id=None, subject_id="s"), sel).known is False
