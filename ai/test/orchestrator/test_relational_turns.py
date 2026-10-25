"""안부·인사·마음만 있는 질문은 기억 질문이 아니다 — 사전 판정·가드가 '모른다·되묻기'로 바꾸지 않는다.

2026-10-17 실사용: 고인 페르소나에게 "할머니 편안하게 잘 계시죠?"라고 묻자 사전 판정이 '기억에 없음' 지시를 넣고,
가드가 근거 없는 답을 "글쎄, 그건 기억이 잘 안 나네. 네가 얘기 좀 해 줄래?"로 갈아 끼웠다.
아래는 (1) 그 경우가 풀렸는지, (2) 화제가 섞인 진짜 질문과 지어낸 숫자·고유명사는 전과 같이 잡히는지를 고정한다.
가짜 데이터만. kiwipiepy 필요.
"""
import importlib.util
import unittest
from pathlib import Path

from orchestrator.contracts import (
    Generation,
    MemoryItem,
    MemorySelection,
    PersonaProfile,
    SessionState,
    TurnInput,
)
from orchestrator.guard import ASK_BACK, GuardV1, GuardV11
from orchestrator.persona import PersonaRegistry
from orchestrator.precheck import CoveragePrecheck, CoveragePrecheckV11, strip_relational

HAS_KIWI = importlib.util.find_spec("kiwipiepy") is not None
PDIR = Path(__file__).parent / "fixtures" / "personas_restricted"
PERSONA = "당신은 홍길동의 페르소나를 가진 챗봇이다.\n\n# 기본 정보\n홍길동. 남원 출신.\n"
MEMS = [MemoryItem(id="a", title="된장찌개", text="된장찌개는 멸치 육수에 두부를 넣고 끓였다."),
        MemoryItem(id="b", title="손자", text="손자 철수는 대학생이다.")]
SEL = MemorySelection(memories=MEMS, strategy="t")

# 기억을 묻지 않는 말(판정하지 않아야 함)
RELATIONAL = [
    "할머니 저 잘 지내고 있어요 할머니 편안하게 잘 계시죠?",   # 실사용 화면 그대로
    "할머니 잘 계세요?",
    "거기는 어때?",
    "할머니 편히 지내시죠?",
    "어디 아픈 데는 없어?",
    "할머니 보고 싶어요. 잘 지내시죠?",
    "할머니 안녕하세요?",
]
# 화제가 있는 질문(전처럼 판정해야 함)
TOPICAL = [
    "된장찌개 어떻게 끓였어?",
    "우리 제주도 갔던 거 기억나?",
    "할머니 잘 계시죠? 근데 우리 제주도 갔던 거 기억나?",   # 안부 + 진짜 질문
    "할머니 잘하시던 요리가 뭐였지?",                        # '잘하시던' 이 떼여도 '요리'가 남는다
]
# 관계 말처럼 보이지만 기억을 묻는 질문(검토에서 나온 오탐) + 뗄 말이 없는 순수 의문사 질문 — 판정이 전과 같아야 한다
NOT_RELATIONAL = [
    "할머니 잘하시던 거 뭐야?", "잘하시던 게 뭐였어?", "잘 사시던 데 어디야?", "잘 계시던 데 어디야?",
    "할머니 편안하게 잘 지내셨던 데가 어디야?", "밥 먹었던 데 어디야?", "식사하신 데 어디였어?",
    "고마운 분 누구였어?", "고마웠던 데가 어디야?", "할머니 거기 좋다던 데가 어디였지?",
    "감사원이 뭐야?", "미안마을 어디야?", "안녕동 살 때 기억나?", "반가사유상 보러 간 거 기억나?",
    "고마웠던 사람 누구야?", "그리워하던 사람 누구야?", "사랑하던 사람 누구야?", "보고 싶던 사람 누구야?",
    "할머니 감사하다던 사람 누구야?", "할머니가 미안하다고 했던 거 뭐야?", "거기 좋았지? 우리 갔던 데",
    "누구였어?", "언제였지?", "그게 뭐였지?", "어디 갔었어?", "그 사람 누구야?",
]


def g(text):
    return Generation(text=text, provider="t", model="t")


def turn(msg, **kw):
    return TurnInput(subject_id="s", message=msg, persona_instructions=PERSONA, be_memories=MEMS, **kw)


def st(**kw):
    return SessionState(session_id="x", subject_id="s", counters={}, **kw)


class StripRelationalTest(unittest.TestCase):
    def test_no_catastrophic_backtracking(self) -> None:  # "잘잘잘…?" 류에서 지수 시간이 걸리던 정규식
        import time
        for s in ("잘" * 26 + "?", "ㅋ잘" * 24 + "?", "잘괜찮" * 400 + "?", "편안" * 3000 + "계시"):
            t = time.perf_counter()
            strip_relational(s)
            self.assertLess(time.perf_counter() - t, 0.5, s[:20])

    def test_strips_only_relational_words(self) -> None:
        self.assertEqual(strip_relational("할머니 편안하게 잘 계시죠?"), "할머니")
        self.assertEqual(strip_relational("저 잘 지내고 있어요"), "저")
        self.assertIn("제주도", strip_relational("잘 계시죠? 근데 우리 제주도 갔던 거 기억나?"))
        self.assertIn("요리", strip_relational("할머니 잘하시던 요리가 뭐였지?"))


@unittest.skipUnless(HAS_KIWI, "kiwipiepy 없음")
class PrecheckRelationalTest(unittest.TestCase):
    def test_relational_gets_no_directive(self) -> None:
        reg = PersonaRegistry(PDIR)
        for cls in (CoveragePrecheck, CoveragePrecheckV11):
            for msg in RELATIONAL:
                r = cls(reg).check(turn(msg), st(), SEL)
                self.assertIsNone(r.directive, f"{cls.__name__}: {msg} → {r.topic_terms}")

    def test_topical_still_judged(self) -> None:
        reg = PersonaRegistry(PDIR)
        for cls in (CoveragePrecheck, CoveragePrecheckV11):
            self.assertTrue(cls(reg).check(turn("된장찌개 어떻게 끓였어?"), st(), SEL).known, cls.__name__)
            for msg in TOPICAL[1:]:
                r = cls(reg).check(turn(msg), st(), SEL)
                self.assertTrue(r.topic_terms, f"{cls.__name__}: {msg}")
            r = cls(reg).check(turn("할머니 잘 계시죠? 근데 우리 제주도 갔던 거 기억나?"), st(), SEL)
            self.assertIsNotNone(r.directive, cls.__name__)  # 제주도 기억은 없다 → 전처럼 '모른다' 지시


def _snapshot(msg):
    """(사전 판정 v1·v1.1 지시 여부, 가드 v1·v1.1 결과) — 패치 전 동작과 비교하는 데 쓴다."""
    reg = PersonaRegistry(PDIR)
    pre = tuple(bool(c(reg).check(turn(msg), st(), SEL).directive) for c in (CoveragePrecheck, CoveragePrecheckV11))
    gd = tuple(G(reg).apply(g(a), turn(msg), st(), SEL).action for G in (GuardV1, GuardV11)
               for a in ("[근거: 없음]\n그 동네는 정말 좋았지.", "[근거: m9]\n그건 참 좋았지."))
    return pre, gd


@unittest.skipUnless(HAS_KIWI, "kiwipiepy 없음")
class NotRelationalTest(unittest.TestCase):
    def test_memory_questions_still_judged(self) -> None:
        for msg in NOT_RELATIONAL:
            pre, gd = _snapshot(msg)
            self.assertTrue(all(a == "ask_back" for a in gd), f"{msg}: 가드 {gd}")


@unittest.skipUnless(HAS_KIWI, "kiwipiepy 없음")
class GuardRelationalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reg = PersonaRegistry(PDIR)
        cls.guards = (GuardV1(reg), GuardV11(reg))

    def run_all(self, answer, msg, state=None):
        return [gd.apply(g(answer), turn(msg), state or st(), SEL) for gd in self.guards]

    def test_screen_case_passes(self) -> None:  # 실사용 화면: 근거 없음 헤더 / 엉뚱한 인용 둘 다
        msg = RELATIONAL[0]
        for ans in ("[근거: 없음]\n그래, 할머니는 여기서 편안하게 잘 있다. 너 잘 지낸다니 됐다.",
                    "[근거: m1]\n그래, 할머니는 편안하다. 손자 잘 지낸다니 됐다."):
            for r in self.run_all(ans, msg):
                self.assertNotEqual(r.action, "ask_back", r.reasons)
                self.assertNotEqual(r.text, ASK_BACK["family"][0])
                self.assertIn("relational_skip_r1a_r1t", r.reasons)
                self.assertFalse(r.text.startswith("[근거"))  # 헤더는 여전히 뗀다

    def test_all_relational_pass_with_no_ground(self) -> None:
        for msg in RELATIONAL:
            for r in self.run_all("[근거: 없음]\n그래, 나는 잘 있다. 너도 잘 지내라.", msg):
                self.assertNotEqual(r.action, "ask_back", f"{msg} {r.reasons}")

    def test_speaker_name_counts_as_ground(self) -> None:  # 자기소개한 손주 이름을 불러도 지어냄이 아니다
        for gd in self.guards:
            r = gd.apply(g("[근거: 없음]\n그래, 우리 준혁이 잘 지낸다니 됐다."), turn("할머니 잘 계시죠?"),
                         st(speaker="준혁"), SEL)
            self.assertNotEqual(r.action, "ask_back", r.reasons)

    def test_relational_still_blocks_made_up_specifics(self) -> None:  # 손실 없음: 안부 답에서 지어낸 숫자·지명은 전처럼 막는다
        for r in self.run_all("[근거: 없음]\n그래, 어제 부산 해운대에 3번이나 다녀왔다.", "할머니 잘 계시죠?"):
            self.assertEqual(r.action, "ask_back")
            self.assertTrue(any(x.startswith("R1b_unsupported") for x in r.reasons), r.reasons)

    def test_topical_questions_unchanged(self) -> None:  # 손실 없음: 화제 질문은 전과 같이 R1a·R1t
        for r in self.run_all("[근거: 없음]\n된장은 3년 묵혔지.", "된장찌개 어떻게 끓였어?"):
            self.assertEqual(r.action, "ask_back")
            self.assertIn("R1a_no_ground", r.reasons)
        for r in self.run_all("[근거: m1]\n제주도 바닷가 좋았지.", "할머니 잘 계시죠? 근데 우리 제주도 갔던 거 기억나?"):
            self.assertEqual(r.action, "ask_back")
            self.assertIn("R1t_topic_not_in_cited", r.reasons)

    def test_love_and_care_rules_still_apply(self) -> None:  # 손실 없음: 사랑·챙김 반복 제한은 안부 턴에도 그대로
        for r in self.run_all("[근거: 없음]\n그래, 잘 있다. 사랑한다. 밥 잘 챙겨 먹어라.", "할머니 잘 계시죠?",
                              st().model_copy(update={"counters": {"care": 2}})):
            self.assertNotIn("사랑", r.text)
            self.assertNotIn("밥", r.text)


if __name__ == "__main__":
    unittest.main()
