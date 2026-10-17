"""존댓말 듣는 사람에게 높인 되묻기(가드·언어 가드·사전 판정). 가짜 데이터만.

가족 표 '지금 대화하는 사람' 줄이 존댓말이면 높인 문구, 반말이거나 말 높이를 모르면 예전 문구 그대로다.
"""
import importlib.util
import unittest

from infra.schema.subject import IntakeContext, SubjectContext
from orchestrator.contracts import Generation, MemoryItem, MemorySelection, SessionState, TurnInput
from orchestrator.guard import ASK_BACK, DONT_KNOW, GuardV1, GuardV11, PassGuard
from orchestrator.lang_guard import ASK_BACK as LANG_ASK_BACK
from orchestrator.lang_guard import LanguageGuard
from orchestrator.persona import PersonaRegistry
from orchestrator.precheck import _directive
from orchestrator.text import listener_speech_level
from pipeline.persona_prompt.from_intake import spec_from_intake
from pipeline.persona_prompt.render import render_layered_prompt
from pipeline.s3_synthesis.c_build_system_prompt import assemble_persona_prompt

HAS_KIWI = importlib.util.find_spec("kiwipiepy") is not None

SUBJECT = {"subject": {"name": "김순자", "addressTerm": "할머니"}}
OWNER = {"name": "홍지은", "relation": "손녀", "addressTermTo": "우리 강아지", "speechLevel": "반말", "isOwner": True}
MEMS = [MemoryItem(id="a", title="된장찌개", text="된장찌개는 멸치 육수에 두부를 넣고 끓였다.")]
SEL = MemorySelection(memories=MEMS, strategy="t")
# 바꾸기 전 _directive(["제주도"], False) 그대로
OLD_FAMILY_DIRECTIVE = ("# 이번 질문에 대해 (코드 판정)\n"
                        "가족이 물은 것(제주도)은 기억에 없다. 지어내지 말고 \"글쎄, 그건 기억이 잘 안 나네\"처럼 "
                        "모른다고 짧게 말한 뒤, \"네가 얘기 좀 해 줄래?\"처럼 가족에게 되물어라. "
                        "장소·날짜·이름 같은 세부를 만들지 않는다.")


def _rows(level: str | None, *, owner: bool = True) -> list[dict]:
    listener = {"name": "박민호", "relation": "사위", "addressTermTo": "박 서방", "addressTermFrom": "장모님",
                "talksWithPersona": True, "isListener": True}
    if level:
        listener["speechLevel"] = level
    return [listener, OWNER] if owner else [listener]


def _intake(rows) -> IntakeContext:
    return IntakeContext.model_validate(
        {"basicProfile": {"status": "사망"}, "audience": "나와 내가 초대한 사람만", "familyMap": rows})


def _layered(level: str | None, *, owner: bool = True) -> str:
    return render_layered_prompt(spec_from_intake(SubjectContext(**SUBJECT), _intake(_rows(level, owner=owner))))


class ListenerSpeechLevelTest(unittest.TestCase):
    def test_reads_listener_row_of_layered_text(self) -> None:
        self.assertEqual(listener_speech_level(_layered("존댓말")), "존댓말")
        self.assertEqual(listener_speech_level(_layered("반말")), "반말")
        self.assertEqual(listener_speech_level(_layered("존댓말", owner=False)), "존댓말")  # 주인 자기 조립본도 같다

    def test_none_when_unknown(self) -> None:
        self.assertIsNone(listener_speech_level(_layered(None)))  # 주인 줄의 '반말'을 듣는 사람 것으로 읽지 않는다
        no_listener = render_layered_prompt(spec_from_intake(SubjectContext(**SUBJECT), _intake([OWNER])))
        self.assertIsNone(listener_speech_level(no_listener))
        self.assertIsNone(listener_speech_level(""))
        self.assertIsNone(listener_speech_level("당신은 홍길동의 페르소나를 가진 챗봇이다.\n\n# 기본 정보\n홍길동."))

    def test_legacy_text_has_no_level(self) -> None:
        legacy = assemble_persona_prompt(subject_context=SubjectContext(**SUBJECT), persons_results=[],
                                         sensitivity_results=[], segments_by_recording=[], subject_labels=[],
                                         intake_context=_intake(_rows("존댓말")))
        self.assertIn("(지금 대화하는 사람)", legacy)
        self.assertIsNone(listener_speech_level(legacy))

    def test_basic_info_line_is_not_a_family_row(self) -> None:
        text = "# 기본 정보\n- 지금 대화하는 사람(박민호)이 부르는 말: 장모님 / 존댓말\n"
        self.assertIsNone(listener_speech_level(text))
        self.assertEqual(listener_speech_level('- 박민호: 지금 대화하는 사람 / 톤: 조용함 / 존댓말'), "존댓말")


def _g(text: str) -> Generation:
    return Generation(text=text, provider="t", model="t")


def _turn(instructions: str, msg: str = "된장찌개 어떻게 끓였어요?") -> TurnInput:
    return TurnInput(subject_id="s", message=msg, persona_instructions=instructions, be_memories=MEMS)


def _st(**counters) -> SessionState:
    return SessionState(session_id="x", subject_id="s", counters=counters)


class GuardAskBackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.guards = [GuardV1(PersonaRegistry(None)), GuardV11(PersonaRegistry(None))]

    def ask(self, guard, instructions: str, state: SessionState | None = None) -> str:
        r = guard.apply(_g("[근거: 없음]\n된장은 3년 묵혔지."), _turn(instructions), state or _st(), SEL)
        self.assertEqual(r.action, "ask_back")
        return r.text

    def test_polite_listener_gets_polite_ask_back(self) -> None:
        for guard in self.guards:
            self.assertEqual(self.ask(guard, _layered("존댓말")), ASK_BACK["family_polite"][0])
            self.assertEqual(self.ask(guard, _layered("존댓말"), _st(ask_back_streak=1)), ASK_BACK["family_polite"][1])

    def test_casual_or_unknown_keeps_todays_text(self) -> None:
        self.assertEqual(ASK_BACK["family"], ("글쎄, 그건 기억이 잘 안 나네. 네가 얘기 좀 해 줄래?", "그건 잘 모르겠다."))
        for guard in self.guards:
            for instructions in (_layered("반말"), _layered(None), "당신은 홍길동의 페르소나를 가진 챗봇이다.\n"):
                self.assertEqual(self.ask(guard, instructions), ASK_BACK["family"][0])
                self.assertEqual(self.ask(guard, instructions, _st(ask_back_streak=1)), ASK_BACK["family"][1])

    def test_public_wins_over_polite(self) -> None:
        public = "당신은 김공개 본인의 말투로 말하는 페르소나다.\n# 방문자\n- 박민호: 지금 대화하는 사람 / 존댓말\n"
        self.assertEqual(self.ask(self.guards[0], public), ASK_BACK["public"][0])

    def test_polite_lines_count_as_dont_know(self) -> None:
        self.assertEqual(ASK_BACK["family_polite"],
                         ("글쎄요, 그건 기억이 잘 안 나네요. 이야기 좀 해 주실래요?", "그건 잘 모르겠어요."))
        for line in ASK_BACK["family_polite"]:
            self.assertTrue(DONT_KNOW.search(line), line)


class LangGuardAskBackTest(unittest.TestCase):
    """언어 가드(fix)가 답을 다 지우면 되묻는다. 그 문구도 같은 규칙으로 높인다."""

    def ask(self, instructions: str) -> str:
        g = LanguageGuard(PassGuard(), PersonaRegistry(None), mode="fix")
        r = g.apply(_g("Okay, let me think."), _turn(instructions, "밥은 드셨어요?"), _st(), SEL)
        self.assertEqual(r.action, "ask_back")
        return r.text

    def test_polite_listener_gets_polite_ask_back(self) -> None:
        self.assertEqual(LANG_ASK_BACK["family_polite"], "네? 다시 한 번 말해 주실래요?")
        self.assertEqual(self.ask(_layered("존댓말")), LANG_ASK_BACK["family_polite"])

    def test_casual_or_unknown_keeps_todays_text(self) -> None:
        self.assertEqual(LANG_ASK_BACK["family"], "응? 다시 말해 줄래?")
        for instructions in (_layered("반말"), _layered(None), "당신은 홍길동의 페르소나를 가진 챗봇이다.\n"):
            self.assertEqual(self.ask(instructions), LANG_ASK_BACK["family"])

    def test_public_wins_over_polite(self) -> None:
        public = "당신은 김공개 본인의 말투로 말하는 페르소나다.\n# 방문자\n- 박민호: 지금 대화하는 사람 / 존댓말\n"
        self.assertEqual(self.ask(public), LANG_ASK_BACK["public"])


class DirectiveTest(unittest.TestCase):
    def test_family_directive_unchanged(self) -> None:
        self.assertEqual(_directive(["제주도"], False), OLD_FAMILY_DIRECTIVE)
        self.assertEqual(_directive(["제주도"], False, polite=False), OLD_FAMILY_DIRECTIVE)

    def test_polite_directive(self) -> None:
        d = _directive(["제주도"], False, polite=True)
        self.assertIn("가족이 물은 것(제주도)은 기억에 없다.", d)
        self.assertIn("\"글쎄요, 그건 기억이 잘 안 나네요\"처럼 모른다고 짧게 말한 뒤, "
                      "\"이야기 좀 해 주실래요?\"처럼 존댓말로 가족에게 되물어라.", d)
        self.assertNotIn("네가", d)
        self.assertTrue(d.endswith("장소·날짜·이름 같은 세부를 만들지 않는다."))

    def test_public_ignores_polite(self) -> None:
        self.assertEqual(_directive(["제주도"], True, polite=True), _directive(["제주도"], True))


@unittest.skipUnless(HAS_KIWI, "kiwipiepy not installed")
class PrecheckPoliteTest(unittest.TestCase):
    MSG = "장모님, 우리 예전에 제주도 같이 갔던 거 기억나세요?"

    def check(self, cls, instructions: str):
        turn = TurnInput(subject_id="s", message=self.MSG, persona_instructions=instructions, speaker_hint="박민호")
        r = cls().check(turn, SessionState(session_id=None, subject_id="s", speaker="박민호"), SEL)
        self.assertFalse(r.known)
        return r.directive

    def test_both_precheck_versions_pick_directive_by_listener_level(self) -> None:
        from orchestrator.precheck import CoveragePrecheck, CoveragePrecheckV11
        for cls in (CoveragePrecheck, CoveragePrecheckV11):
            self.assertIn("존댓말로 가족에게 되물어라", self.check(cls, _layered("존댓말")))
            for level in ("반말", None):
                d = self.check(cls, _layered(level))
                self.assertIn("\"네가 얘기 좀 해 줄래?\"처럼 가족에게 되물어라", d)
                self.assertNotIn("존댓말로", d)


if __name__ == "__main__":
    unittest.main()
