"""고인 페르소나는 '기억이 안 난다'·'모르겠다'고 말하지 않는다(잠금 2, 대표 지시 2026-10-21).

기억에 없는 것을 물으면 지어내지 않고, 잊은 듯 말하지도 않고, 가족에게서 그 이야기를 듣고 싶어 하며 되묻는다.
살아 계신 분은 예전 문구 그대로다.
"""
import unittest

from orchestrator.contracts import Generation, MemoryItem, MemorySelection, SessionState, TurnInput
from orchestrator.guard import ASK_BACK, DONT_KNOW, GuardV1, GuardV11
from orchestrator.persona import PersonaRegistry
from orchestrator.precheck import _directive

FORGET = ("기억이 안", "기억이 잘 안", "모르겠", "잊어버", "까먹")
MEMS = [MemoryItem(id="m1", title="장 담그기", text="해마다 늦가을에 메주를 쑤어 장을 담갔다.")]
SEL = MemorySelection(memories=MEMS, strategy="t")
PERSONA = "# 페르소나\n너는 손녀 지우의 할머니다. 반말로 짧게 말한다."


def _ask(guard, *, deceased, polite=False):
    instructions = PERSONA + ("\n# 지금 대화하는 사람\n- 사위 박민호, 그분은 존댓말을 썼다" if polite else "")
    turn = TurnInput(subject_id="s", message="된장찌개 어떻게 끓였어?", persona_instructions=instructions,
                     be_memories=MEMS, deceased=deceased)
    r = guard.apply(Generation(text="[근거: 없음]\n된장은 3년 묵혔지.", provider="t", model="t"), turn,
                    SessionState(session_id="x", subject_id="s"), SEL)
    return r


class DeceasedAskBackTest(unittest.TestCase):
    def test_deceased_variants_never_claim_forgetting(self) -> None:
        for kind in ("family_deceased", "family_polite_deceased"):
            for line in ASK_BACK[kind]:
                with self.subTest(kind=kind, line=line):
                    self.assertFalse(any(w in line for w in FORGET), line)
                    self.assertTrue(DONT_KNOW.search(line), "가드가 되묻기로 알아봐야 지어내기 검사에서 빠진다")

    def test_guard_uses_deceased_ask_back(self) -> None:
        for guard in (GuardV1(PersonaRegistry(None)), GuardV11(PersonaRegistry(None))):
            r = _ask(guard, deceased=True)
            self.assertEqual(r.action, "ask_back")
            self.assertEqual(r.text, ASK_BACK["family_deceased"][0])
            self.assertFalse(any(w in r.text for w in FORGET))

    def test_living_keeps_todays_text(self) -> None:
        r = _ask(GuardV1(PersonaRegistry(None)), deceased=False)
        self.assertEqual(r.text, ASK_BACK["family"][0])


class DeceasedDirectiveTest(unittest.TestCase):
    def test_deceased_directive_forbids_forgetting_and_fabrication(self) -> None:
        for polite in (False, True):
            d = _directive(["노래"], public=False, polite=polite, deceased=True)
            with self.subTest(polite=polite):
                self.assertNotIn("기억이 잘 안 나네", d)
                self.assertIn("잊은 듯 말하지도 말고", d)
                self.assertIn("지어내지 말고", d)
                self.assertIn("듣고 싶", d)
                self.assertEqual("존댓말로" in d, polite)

    def test_living_directive_unchanged(self) -> None:
        self.assertIn("글쎄, 그건 기억이 잘 안 나네", _directive(["노래"], public=False))
        self.assertIn("그건 제가 잘 모르겠네요", _directive(["노래"], public=True, deceased=True))


if __name__ == "__main__":
    unittest.main()
