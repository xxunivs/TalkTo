"""언어 누출 가드: 맥락에 없던 문자·영어 낱말·추론 문장만 잡고, 영어로 대화하면 영어 답은 통과."""
import unittest

from orchestrator.contracts import Generation, GuardResult, MemoryItem, MemorySelection, SessionState, TurnInput
from orchestrator.guard import PassGuard
from orchestrator.lang_guard import LanguageGuard, findings, scripts
from orchestrator.persona import PersonaRegistry

SEL = MemorySelection(memories=[MemoryItem(id="m1", text="KTX 타고 부산 갔다")], strategy="t")


def run(answer: str, message: str = "할머니 뭐 해?", mode: str = "fix") -> GuardResult:
    turn = TurnInput(subject_id="s", message=message, persona_instructions="당신은 김할매의 페르소나다. AI라고 밝히지 않는다.")
    g = LanguageGuard(PassGuard(), PersonaRegistry(None), mode=mode)
    return g.apply(Generation(text=answer, provider="t", model="t"), turn, SessionState(session_id=None, subject_id="s"), SEL)


class FindingsTest(unittest.TestCase):
    def test_scripts(self) -> None:
        self.assertEqual(scripts("밥 123 ok 好 яда"), {"hangul", "latin", "han", "cyrillic"})

    def test_words_from_context_pass(self) -> None:
        self.assertEqual(findings("KTX 타고 갔구나.", "KTX 타고 부산 갔다"), [])

    def test_new_script_and_words_flagged(self) -> None:
        self.assertEqual(findings("물 3컵 પાણી 넣어.", "된장찌개"), ["script:gujarati"])
        self.assertEqual(findings("Well 챙겨 먹어.", "밥 먹었어"), ["latin:Well"])
        self.assertIn("leak:wait", findings("어, 그래. Wait Korean only.", "콩나물국"))

    def test_ok_is_not_a_leak(self) -> None:
        self.assertEqual(findings("OK, 알았다.", "밥 먹었어"), [])

    def test_english_dialogue_allows_english(self) -> None:
        q = "Grandma, can you answer me in English?"
        self.assertEqual(findings("Of course, dear.", q, q), [])
        self.assertEqual(findings("The user wants English.", q, q), ["leak:the user"])


class GuardTest(unittest.TestCase):
    def test_clean_answer_untouched(self) -> None:
        r = run("어, 부산 잘 다녀왔다.")
        self.assertEqual((r.text, r.action, r.reasons), ("어, 부산 잘 다녀왔다.", "pass", []))

    def test_flag_mode_keeps_text(self) -> None:
        r = run("어, 밥 먹었다. Wait 이건 아니다.", mode="flag")
        self.assertEqual(r.text, "어, 밥 먹었다. Wait 이건 아니다.")
        self.assertIn("lang:latin:Wait", r.reasons)

    def test_fix_mode_drops_sentence(self) -> None:
        r = run("어, 밥 먹었다. Wait, Korean only.")
        self.assertEqual((r.text, r.action), ("어, 밥 먹었다.", "trim"))

    def test_fix_mode_all_dropped_asks_back(self) -> None:
        self.assertEqual(run("Okay, let me think.").action, "ask_back")

    def test_english_question_english_answer_passes(self) -> None:
        r = run("Of course, sweetheart.", message="Grandma, can you answer me in English?")
        self.assertEqual(r.action, "pass")


if __name__ == "__main__":
    unittest.main()
