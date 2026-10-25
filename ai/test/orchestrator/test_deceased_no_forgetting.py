"""고인 페르소나는 '기억이 안 난다'·'모르겠다'고 말하지 않는다(잠금 2, 대표 지시 2026-10-21).

기억에 없는 것을 물으면 지어내지 않고, 잊은 듯 말하지도 않고, 가족에게서 그 이야기를 듣고 싶어 하며 되묻는다.
살아 계신 분은 예전 문구 그대로다.
지키는 곳: 사전 판정 지시·가드 되묻기 문구, 대답 거르기(가드 모든 반환·기존 경로 generate_persona_response),
L1 규칙(unknown_user·unknown_facts 고인 변형), 설문 상황별 반응(M8-08 등). 실제 API 호출 없음, 가짜 데이터만.
"""
import unittest
from itertools import product
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from app.schema.persona import PersonaResponseRequest
from app.schema.subject import IntakeContext, SubjectContext
from app.setting import Settings
from orchestrator.contracts import Generation, MemoryItem, MemorySelection, SessionState, TurnInput
from orchestrator.guard import (
    ASK_BACK,
    ASK_BACK_TOPIC,
    DONT_KNOW,
    TELL_ME,
    GuardV1,
    GuardV11,
    drop_forgetting,
    forgot,
    says_forgot,
)
from orchestrator.persona import PersonaRegistry
from orchestrator.precheck import _directive
from pipeline.persona_prompt.from_intake import spec_from_intake
from pipeline.persona_prompt.render import render_layered_prompt
from pipeline.s3_synthesis.c_build_system_prompt import (
    assemble_persona_prompt,
    generate_persona_response,
)
from pipeline.s3_synthesis.common_rules import DEATH_POLICIES, render_rules

FORGET = ("기억이 안", "기억이 잘 안", "모르겠", "잊어버", "까먹")
MEMS = [MemoryItem(id="m1", title="장 담그기", text="해마다 늦가을에 메주를 쑤어 장을 담갔다.")]
SEL = MemorySelection(memories=MEMS, strategy="t")
PERSONA = "# 페르소나\n너는 손녀 지우의 할머니다. 반말로 짧게 말한다."
# "된장찌개 어떻게 끓였어?"에 되묻는 첫 문구. 고인은 화제어를 넣지 않는다(병명·자세한 말을 되받지 않게, 10/24 리뷰)
DEC_FIRST = ASK_BACK["family_deceased"][0]


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
                    self.assertTrue(TELL_ME.search(line), "고인은 이야기를 청하는 되묻기일 때만 지어내기 검사에서 빠진다")

    def test_guard_uses_deceased_ask_back(self) -> None:
        for guard in (GuardV1(PersonaRegistry(None)), GuardV11(PersonaRegistry(None))):
            r = _ask(guard, deceased=True)
            self.assertEqual(r.action, "ask_back")
            self.assertEqual(r.text, DEC_FIRST)
            self.assertNotIn("된장찌개", r.text)
            self.assertFalse(any(w in r.text for w in FORGET))
            self.assertFalse(says_forgot(r.text))

    def test_living_keeps_living_wording(self) -> None:
        r = _ask(GuardV1(PersonaRegistry(None)), deceased=False)
        self.assertEqual(r.text, ASK_BACK_TOPIC["family"].format(topic="된장찌개"))


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


# ---------------------------------------------------------------- 대답 거르기

POLITE = PERSONA + "\n- 박민호 (사위): 지금 대화하는 사람 / 존댓말"
PUBLIC = "당신은 홍길동 본인의 말투로 말하는 페르소나다.\n# 방문자\n- 누가 올지 모른다."
CRISIS = "할머니 나 너무 힘들어서 죽고 싶어요"


class ForgotPatternTest(unittest.TestCase):
    def test_first_person_forgetting(self) -> None:
        for s in ("에이, 잘 기억이 안 나네.", "글쎄, 기억이 가물가물하구나.", "다 잊어버렸다.", "그건 까먹었다.",
                  "할미가 다 잊었다.", "기억이 나지 않는구나.", "생각이 잘 안 나네.", "그건 내가 잘 모르겠다.",
                  "글쎄 잘 모르겠네, 미안하구나", "그건 잘 모르겠어요.", "글쎄요, 그건 기억이 잘 안 나네요.",
                  "기억이 안 나네, 네가 얘기 좀 해 줄래?", "나도 다 잊어버렸다, 너는 기억하니?",
                  # 난다·났어·납니다, 사이에 든 말(하나도·전혀·통), 버렸·먹었
                  "그건 기억이 안 난다.", "기억이 잘 안 난다.", "기억이 안 났어.", "기억이 안 납니다.", "기억 안 난다.",
                  "생각이 안 난다.", "기억이 하나도 안 나.", "기억이 전혀 안 나는구나.", "기억이 통 안 나네.",
                  "잊어 버렸다.", "잊어먹었다.", "까먹어 버렸구나.", "기억을 잘 못 하겠다.", "기억이 안 나서 미안하다.",
                  # 혼잣말로 묻는 꼴, 듣는 사람이 한 말을 내가 잊은 꼴
                  "아이고, 왜 이렇게 기억이 안 나지?", "내가 그걸 잊어버렸나?", "기억이 잘 안 나네?",
                  "기억이 잘 안 나네요?", "잘 모르겠네?",
                  "네가 해 준 얘기는 기억이 잘 안 나네.", "네가 말한 거 다 까먹었다.",
                  # 꾸밈말 둘(이젠 잘), 기억에 없다·깜빡했다·다 잊어서·가물거려
                  "기억이 이젠 잘 안 나.", "생각이 요즘 통 안 나네.", "기억이 다 안 나.", "그건 기억에 없구나.",
                  "그런 기억이 없다.", "깜빡했다.", "다 잊어서 그래.", "가물거려."):
            with self.subTest(s):
                self.assertTrue(forgot(s))

    def test_questions_care_and_idioms_are_not_forgetting(self) -> None:
        for s in ("너 그거 까먹었니?", "잊었어?", "기억 안 나지?", "잊지 마라.", "약 먹는 거 잊어버리지 마라.",
                  "기억이 안 나면 적어 둬라.", "안 잊었다.", "하나도 안 잊어버렸지.", "너 또 까먹었구나.",
                  "넌 잘 모르겠지만 다 지나간다.", "얼마나 보고 싶은지 모르겠다.", "고마워서 어쩔 줄 모르겠다.",
                  "아무 생각이 안 나더라.", "기억은 잘 안 나도 109에 꼭 전화해라.", "밥은 먹었니?",
                  # 그리움·바람·되묻는 말(고인이 슬픈 턴에 하는 말)
                  "할미도 네 생각이 안 나는 날이 없다.", "내가 너를 잊었을 리가 있겠니.", "내가 너를 잊었겠니?",
                  "힘든 일은 다 잊었으면 좋겠다.", "아픈 건 다 잊어버렸으면 좋겠구나.", "잘 모르겠으면 엄마한테 물어봐라.",
                  "잊었던 일이 다 생각나는구나.", "고마워서 어쩔 줄을 모르겠다.", "못잊었지.", "기억이 안 나도 괜찮다.",
                  "보고 싶어서 다른 생각이 안 나더라.", "내가 잊었을까 봐 걱정했니?", "내가 그걸 모르겠나?",
                  "너도 다 잊었구나.", "안 깜빡했다.", "너 또 깜빡했구나.", "깜빡했으면 말해라.",
                  "네 생각이 요즘 안 나는 날이 없다."):
            with self.subTest(s):
                self.assertFalse(forgot(s))

    def test_ask_back_lines_pass(self) -> None:
        for kind in ("family_deceased", "family_polite_deceased"):
            for line in ASK_BACK[kind]:
                self.assertFalse(says_forgot(line), line)

    def test_drop_keeps_other_sentences_and_falls_back(self) -> None:
        self.assertEqual(drop_forgetting("에이 그랬나. 잘 기억이 안 나네. 밥은 먹었니?", "X"), "에이 그랬나. 밥은 먹었니?")
        self.assertEqual(drop_forgetting("글쎄, 기억이 가물가물하구나.", "X"), "X")
        same = "그래, 밥은 먹었니?\n요즘 춥다."
        self.assertIs(drop_forgetting(same, "X"), same)  # 지울 것이 없으면 글자 그대로


def _guard(text: str, msg: str = "오늘 장 보고 왔어.", *, deceased: bool | None = True,
           instructions: str = PERSONA, streak: int = 0, guard=None):
    turn = TurnInput(subject_id="s", message=msg, persona_instructions=instructions, be_memories=MEMS,
                     deceased=deceased)
    st = SessionState(session_id="x", subject_id="s", counters={"ask_back_streak": streak} if streak else {})
    return (guard or GuardV11(PersonaRegistry(None))).apply(Generation(text=text, provider="t", model="t"),
                                                            turn, st, SEL)


class DeceasedGuardOutputTest(unittest.TestCase):
    def test_non_question_turn_drops_the_forgot_sentence(self) -> None:
        for guard in (GuardV1(PersonaRegistry(None)), GuardV11(PersonaRegistry(None))):
            r = _guard("에이 그랬나. 잘 기억이 안 나네. 밥은 먹었니?", guard=guard)
            self.assertEqual((r.text, r.action), ("에이 그랬나. 밥은 먹었니?", "trim"))
            self.assertIn("forgot_drop", r.reasons)

    def test_nothing_left_becomes_deceased_ask_back(self) -> None:
        r = _guard("[근거: 없음]\n글쎄, 기억이 가물가물하구나.")
        self.assertEqual((r.text, r.action), (ASK_BACK["family_deceased"][0], "ask_back"))
        self.assertEqual(r.counter_delta.get("ask_back_streak"), 1)
        self.assertEqual(_guard("글쎄, 기억이 가물가물하구나.", instructions=POLITE).text,
                         ASK_BACK["family_polite_deceased"][0])
        self.assertEqual(_guard("글쎄, 기억이 가물가물하구나.", streak=1).text, ASK_BACK["family_deceased"][1])

    def test_forgot_line_no_longer_hides_fabrication(self) -> None:
        reply = "[근거: 없음]\n글쎄, 기억이 가물가물하구나. 된장은 3년 묵혔지."
        r = _guard(reply, "된장찌개 어떻게 끓였어?")
        self.assertEqual((r.text, r.action), (DEC_FIRST, "ask_back"))
        # 살아 계신 분은 예전 그대로('모른다'로 보고 지어내기 검사를 건너뛴다)
        living = _guard(reply, "된장찌개 어떻게 끓였어?", deceased=False)
        self.assertEqual((living.text, living.action), ("글쎄, 기억이 가물가물하구나. 된장은 3년 묵혔지.", "pass"))

    def test_declarative_forms_are_dropped(self) -> None:
        for guard in (GuardV1(PersonaRegistry(None)), GuardV11(PersonaRegistry(None))):
            r = _guard("그건 기억이 안 난다.", guard=guard)
            self.assertEqual((r.text, r.action), (ASK_BACK["family_deceased"][0], "ask_back"))
        r = _guard("[근거: 없음]\n글쎄다, 그건 기억이 안 난다. 된장은 3년 묵혔지.", "된장찌개 어떻게 끓였어?")
        self.assertEqual((r.text, r.action), (DEC_FIRST, "ask_back"))

    def test_hedge_does_not_hide_fabrication_for_deceased(self) -> None:
        # 고인은 '글쎄·몰라'가 있어도 지어내기 검사를 한다(이야기를 청하는 되묻기일 때만 건너뛴다)
        reply = "[근거: 없음]\n글쎄다, 그건 몰라. 된장은 3년 묵혔지."
        r = _guard(reply, "된장찌개 어떻게 끓였어?")
        self.assertEqual((r.text, r.action), (DEC_FIRST, "ask_back"))
        living = _guard(reply, "된장찌개 어떻게 끓였어?", deceased=False)
        self.assertEqual((living.text, living.action), ("글쎄다, 그건 몰라. 된장은 3년 묵혔지.", "pass"))
        asked = _guard("[근거: 없음]\n글쎄다, 그 얘기 네가 한번 해 줄래?", "된장찌개 어떻게 끓였어?")
        self.assertEqual((asked.text, asked.action), ("글쎄다, 그 얘기 네가 한번 해 줄래?", "pass"))

    def test_grief_lines_pass_unchanged(self) -> None:
        for msg, reply in (("할머니 보고 싶어요", "할미도 네 생각이 안 나는 날이 없다."),
                           ("할머니 저 잊으셨어요?", "내가 너를 잊었을 리가 있겠니.")):
            with self.subTest(msg):
                r = _guard(reply, msg)
                self.assertEqual((r.text, r.action), (reply, "pass"))
                self.assertNotIn("forgot_drop", r.reasons)

    def test_listener_questions_and_care_lines_are_kept(self) -> None:
        r = _guard("너 그거 까먹었니? 약 먹는 거 잊어버리지 마라.")
        self.assertEqual((r.text, r.action), ("너 그거 까먹었니? 약 먹는 거 잊어버리지 마라.", "pass"))

    def test_crisis_line_is_never_dropped(self) -> None:
        r = _guard("글쎄, 기억이 잘 안 나네. 혼자 견디지 말고 109에 전화해서 이야기해 보자.", CRISIS)
        self.assertEqual(r.text, "혼자 견디지 말고 109에 전화해서 이야기해 보자.")
        self.assertIn("safety_skip", r.reasons)

    def test_death_question_is_not_turned_into_ask_back(self) -> None:
        # 그분의 죽음을 묻는 말에 "그 얘기 해 줄래?"로 되묻지 않는다(지울 문장만 있으면 그대로 둔다)
        r = _guard("그때 일은 기억이 잘 안 나는구나.", "할머니 어떻게 돌아가셨어요?")
        self.assertEqual((r.text, r.action), ("그때 일은 기억이 잘 안 나는구나.", "pass"))
        self.assertIn("forgot_kept_safety", r.reasons)

    def test_living_and_public_are_unchanged(self) -> None:
        reply = "글쎄, 그건 기억이 잘 안 나네. 밥은 먹었니?"
        for kw in ({"deceased": False}, {"deceased": True, "instructions": PUBLIC}):
            with self.subTest(**kw):
                r = _guard(reply, **kw)
                self.assertEqual((r.text, r.action), (reply, "pass"))


def _plain(reply: str, message: str = "할머니 그 노래 생각나?", *, deceased: bool | None = True,
           instructions: str = "너는 할머니다.", fish_emotion: str = "off", voice_id: str | None = None):
    req = PersonaResponseRequest.model_validate({
        "message": message,
        "persona": {"subjectId": "s1", "instructions": instructions, "deceased": deceased,
                    "voiceId": voice_id, "fixedPrompt": True},
    })
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=reply))]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    with patch("app.external_api.openai_client.OpenAI", return_value=client):
        return generate_persona_response(req, settings=Settings(openai_api_key=SecretStr("k"),
                                                                fish_emotion=fish_emotion))


class DeceasedPlainPathTest(unittest.TestCase):
    """가져온 지침(fixedPrompt)은 가드를 거치지 않는다. 기존 경로에서도 같은 문장을 지운다."""

    def test_forgot_sentence_is_dropped(self) -> None:
        r = _plain("에이 그랬나. 잘 기억이 안 나네. 밥은 먹었니?")
        self.assertEqual(r.content, "에이 그랬나. 밥은 먹었니?")

    def test_nothing_left_uses_deceased_ask_back_by_speech_level(self) -> None:
        self.assertEqual(_plain("글쎄, 기억이 가물가물하구나.").content, ASK_BACK["family_deceased"][0])
        self.assertEqual(_plain("글쎄요, 그건 기억이 잘 안 나네요.", instructions=POLITE).content,
                         ASK_BACK["family_polite_deceased"][0])

    def test_spoken_text_follows_the_shown_text(self) -> None:
        r = _plain("[반갑게] 왔니. [작게 웃으며] 그건 기억이 잘 안 나네.", fish_emotion="inline",
                   voice_id="fish_0123abcd")
        self.assertEqual((r.content, r.speech_text), ("왔니.", "[반갑게] 왔니."))
        r = _plain("[감정:warm] 다 잊어버렸다.", fish_emotion="cue", voice_id="fish_0123abcd")
        self.assertEqual((r.content, r.emotion, r.speech_text), (ASK_BACK["family_deceased"][0], None, None))

    def test_declarative_form_is_dropped(self) -> None:
        self.assertEqual(_plain("그건 기억이 안 난다.").content, ASK_BACK["family_deceased"][0])
        self.assertEqual(_plain("그건 기억이 안 난다. 밥은 먹었니?").content, "밥은 먹었니?")

    def test_crisis_and_death_turns_are_not_turned_into_ask_back(self) -> None:
        # 가드와 같다: 위기·죽음 턴에 "그 얘기 해 줄래? 듣고 싶구나"로 되묻지 않는다(지울 문장만 있으면 그대로 둔다)
        for msg, reply in ((CRISIS, "글쎄, 잘 모르겠다."),
                           ("할머니 어떻게 돌아가셨어요?", "그때 일은 기억이 잘 안 나는구나.")):
            with self.subTest(msg):
                r = _plain(reply, msg)
                self.assertEqual(r.content, reply)
                self.assertNotIn(r.content, ASK_BACK["family_deceased"])
        r = _plain("글쎄, 기억이 잘 안 나네. 혼자 견디지 말고 109에 전화해서 이야기해 보자.", CRISIS)
        self.assertEqual(r.content, "혼자 견디지 말고 109에 전화해서 이야기해 보자.")

    def test_living_and_public_are_unchanged(self) -> None:
        reply = "글쎄, 그건 기억이 잘 안 나네."
        self.assertEqual(_plain(reply, deceased=False).content, reply)
        self.assertEqual(_plain(reply, instructions=PUBLIC).content, reply)
        # '# 방문자' 제목이 없는 예전 공개 지시문도 가드와 같게 공개로 본다
        legacy_public = "당신은 홍길동 본인의 말투로 말하는 페르소나다."
        self.assertEqual(_plain(reply, instructions=legacy_public).content, reply)


# ---------------------------------------------------------------- L1 규칙·설문

FORGET_RULE = FORGET + ("기억이 가물", "가물가물", "잊었", "까먹었", "생각이 안 나", "누구더라")


class DeceasedRulesTest(unittest.TestCase):
    def test_deceased_private_rules_never_phrase_not_knowing(self) -> None:
        for set_name, fam, pol in product(("legacy", "layered"), (True, False), DEATH_POLICIES):
            text = render_rules(set_name, public=False, deceased=True, has_family=fam, death_policy=pol,
                                vars={"death_answer": "그렇게 됐지"})
            with self.subTest(set=set_name, family=fam, policy=pol):
                self.assertFalse([w for w in FORGET_RULE if w in text])
                self.assertFalse(says_forgot(text))
                # 아는 척·지어내기 금지는 그대로, 대신 이야기를 청한다
                self.assertIn("**모르는 사람을 아는 척하지 않는다 (중요).**", text)
                self.assertIn("**기억에 없는 일은 아는 척하지 않는다 (중요).**", text)
                self.assertIn("가짜 친밀함(호칭·옛일)을 지어내지 않는다", text)
                self.assertIn("더 들려줄래?", text)

    def test_living_private_rules_keep_todays_text(self) -> None:
        for set_name in ("legacy", "layered"):
            text = render_rules(set_name, public=False, deceased=False, has_family=True)
            self.assertIn("\"그건 내가 잘 모르겠다\"로 먼저 인정한 뒤", text)
            self.assertIn("\"글쎄, 잘 모르겠구나\"로 정중히 인정하고", text)


SUBJECT = {"subject": {"name": "김할매"}}
DONT_KNOW_SIT = "모르는 것을 물으면 (모른다고 할 때는 늘 이 말투로)"


def _intake(status: str = "사망", audience: str = "나와 내가 초대한 사람만",
            response: str = "아이고, 기억이 잘 안 나네") -> IntakeContext:
    return IntakeContext.model_validate({
        "basicProfile": {"status": status}, "audience": audience,
        "situationalReactions": [{"situation": DONT_KNOW_SIT, "response": response},
                                 {"situation": "\"힘들어요\"라고 하면", "response": "\"잘될 거야\""}],
    })


class DeceasedIntakeSituationTest(unittest.TestCase):
    def test_deceased_drops_forgetting_situation(self) -> None:
        for response in ("아이고, 기억이 잘 안 나네", "\"그건 내가 잘 모르겠구나\"", "다 까먹었다", "그건 기억이 안 난다"):
            with self.subTest(response):
                spec = spec_from_intake(SubjectContext(**SUBJECT), _intake(response=response))
                self.assertEqual([s.situation for s in spec.situations], ["\"힘들어요\"라고 하면"])
                self.assertTrue(any("고인 페르소나에서 뺌" in n for n in spec.notes))
                self.assertNotIn(DONT_KNOW_SIT, render_layered_prompt(spec))

    def test_ask_back_answer_is_kept(self) -> None:
        for response in ("그 얘기 좀 해 봐라", "할미가 너를 잊었을 리가 있겠니", "힘든 일은 다 잊었으면 좋겠다"):
            with self.subTest(response):
                spec = spec_from_intake(SubjectContext(**SUBJECT), _intake(response=response))
                self.assertEqual(len(spec.situations), 2)

    def test_living_and_public_keep_it(self) -> None:
        for kw in ({"status": "생존"}, {"audience": "공개"}):
            with self.subTest(**kw):
                spec = spec_from_intake(SubjectContext(**SUBJECT), _intake(**kw))
                self.assertEqual(len(spec.situations), 2)

    def test_legacy_builder_drops_it_too(self) -> None:
        def build(intake):
            return assemble_persona_prompt(subject_context=SubjectContext(**SUBJECT), persons_results=[],
                                           sensitivity_results=[], segments_by_recording=[], subject_labels=[],
                                           intake_context=intake)
        self.assertNotIn("기억이 잘 안 나네", build(_intake()))
        self.assertIn("잘될 거야", build(_intake()))
        self.assertIn("기억이 잘 안 나네", build(_intake(status="생존")))


if __name__ == "__main__":
    unittest.main()
