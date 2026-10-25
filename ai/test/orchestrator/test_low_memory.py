"""기억이 적은 페르소나 — 대화가 '모른다·얘기 좀 해 줄래?'로 닫히지 않게(4층 레포 세션 19 이식, 10/23). 가짜 데이터만.

1. 기억 수 짐작(low_memory.memory_count): BE 가 memoryTotal 을 주면 그 값, 아니면 top_k 보다 적게 받았을 때만 그 수.
2. '기억이 적을 때' 절: 두 경로(기존 경로·중간계층) 모두, 기억이 적을 때만, 말투 덧붙임 바로 앞에. 가져온 지침은 뺀다.
3. 사전 판정: 기억이 적으면 판정하지 않는다(모든 질문이 '모른다'가 되므로).
4. 가드: 기억이 하나도 없으면 '[근거: 없음]'을 되묻기로 바꾸지 않는다. 되묻기는 화제를 넣고, 세 번째부터는 상대 근황으로.
잠금: 비공개 고인은 새 되묻기·새 절에서도 잊은 듯 말하지 않는다(잠금 2), 미성년 규칙·가드는 그대로다.
"""
import importlib.util
import unittest
from itertools import product
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import SecretStr, ValidationError

from infra.schema.persona import PersonaResponseRequest
from infra.setting import Settings
from orchestrator.contracts import Generation, MemoryItem, MemorySelection, PrecheckResult, SessionState, Turn, TurnInput
from orchestrator.guard import ASK_BACK, ASK_BACK_TOPIC, DONT_KNOW, TELL_ME, ask_back_text, says_forgot
from orchestrator.persona import PersonaRegistry
from orchestrator.service import build_assembler, turn_from_request
from pipeline.persona_prompt.low_memory import LOW_MEMORY_HEAD, is_low_memory, low_memory_note, memory_count
from pipeline.s3_synthesis.c_build_system_prompt import generate_persona_response
from pipeline.s3_synthesis.common_rules import render_rules
from pipeline.safety.minor_guard import FALLBACK, MINOR_NOTE
from test.orchestrator.test_deceased_no_forgetting import FORGET_RULE

HAS_KIWI = importlib.util.find_spec("kiwipiepy") is not None
FIXTURES = Path(__file__).parent / "fixtures"
PDIR = FIXTURES / "personas_restricted"
PERSONA = "당신은 홍길동의 페르소나를 가진 챗봇이다.\n\n# 기본 정보\n홍길동. 남원 출신.\n"
POLITE = PERSONA + "\n# 가족\n- 박민호 (사위): 지금 대화하는 사람 / 부르는 말 \"박 서방\" / 존댓말\n"
ONE = [MemoryItem(id="a", title="된장찌개", text="된장찌개는 멸치 육수에 두부를 넣고 끓였다.")]
HEAD = LOW_MEMORY_HEAD
# 고인 문구에 없어야 할 말: 공통 규칙 검사 낱말 + 기억에 없다·깜빡·모른다
NEVER_DECEASED = FORGET_RULE + ("기억에 없다", "깜빡", "모른")
DECEASED_KINDS = ("family_deceased", "family_polite_deceased")


def _turn(msg="밥 먹었어?", memories=ONE, memory_total=None, history=(), deceased=False, **kw):
    return TurnInput(subject_id="s", message=msg, persona_instructions=kw.pop("instructions", PERSONA),
                     be_memories=memories, memory_total=memory_total, history=list(history), deceased=deceased, **kw)


def _sel(memories=ONE):
    return MemorySelection(memories=list(memories), strategy="t")


def _no_forgetting(test: unittest.TestCase, text: str) -> None:
    test.assertFalse(says_forgot(text), text)
    test.assertFalse([w for w in NEVER_DECEASED if w in text], text)


class MemoryCountTest(unittest.TestCase):
    def test_total_wins_then_estimate_only_below_top_k(self) -> None:
        self.assertEqual(memory_count(5, 300), 300)
        self.assertEqual(memory_count(1, None), 1)      # top-5 중 1개만 왔다 = 전부 1개
        self.assertIsNone(memory_count(5, None))        # 5개 다 왔다 = 총수 모름
        self.assertTrue(is_low_memory(0, None))
        self.assertTrue(is_low_memory(5, 3))
        self.assertFalse(is_low_memory(5, None))        # 모르면 적다고 보지 않는다(기억 많은 페르소나 보호)
        self.assertFalse(is_low_memory(2, 40))


class NoteTextTest(unittest.TestCase):
    VARIANTS = [dict(public=p, deceased=d, polite=o) for p in (False, True) for d in (False, True) for o in (False, True)]

    def test_note_mentions_count_and_asks_concretely(self) -> None:
        for kw in self.VARIANTS:
            with self.subTest(**kw):
                t = low_memory_note(2, **kw)
                self.assertTrue(t.startswith(HEAD))
                self.assertIn("기억 2개", t)
                self.assertIn("구체적으로 되물어", t)
                self.assertFalse(any(ln.startswith("- 기억") for ln in t.splitlines()))  # 기억 블록 줄과 섞이지 않게

    def test_deceased_note_never_phrases_forgetting(self) -> None:
        for polite in (False, True):
            with self.subTest(polite=polite):
                t = low_memory_note(0, deceased=True, polite=polite)
                _no_forgetting(self, t)
                self.assertIn("듣고 싶다는 마음", t)
                self.assertIn("지어내지 않고", t)
        self.assertIn("짧게 인정한 뒤", low_memory_note(0))            # 살아 계신 분은 4층 레포 문구 그대로
        self.assertIn("모르겠네요", low_memory_note(0, public=True, deceased=True))  # 공개 본인은 잠금 밖(가드와 같다)

    def test_polite_note_has_no_casual_examples(self) -> None:
        for deceased in (False, True):
            t = low_memory_note(1, deceased=deceased, polite=True)
            self.assertNotIn("줄래", t)
            self.assertNotIn("네가", t)
            self.assertIn("주실래요?", t)


class AskBackTextTest(unittest.TestCase):
    def test_three_steps_for_every_kind(self) -> None:
        for kind in ASK_BACK:
            with self.subTest(kind):
                self.assertEqual(len(ASK_BACK[kind]), 3)
                # 화제어는 살아 계신 분·공개 본인만. 고인은 늘 화제 없는 문구(병명·자세한 말을 되받지 않게)
                if kind in DECEASED_KINDS:
                    self.assertNotIn(kind, ASK_BACK_TOPIC)
                    self.assertEqual(ask_back_text(kind, 0, "된장찌개"), ASK_BACK[kind][0])
                else:
                    self.assertIn("된장찌개", ask_back_text(kind, 0, "된장찌개"))
                self.assertEqual(ask_back_text(kind, 0, ""), ASK_BACK[kind][0])
                self.assertEqual(ask_back_text(kind, 1, "된장찌개"), ASK_BACK[kind][1])
                self.assertEqual(ask_back_text(kind, 5, "된장찌개"), ASK_BACK[kind][2])
                self.assertIn("요즘", ASK_BACK[kind][2])  # 세 번째부터는 상대 근황으로
                self.assertNotIn("얘기 좀 해 줄래", ask_back_text(kind, 0, "된장찌개"))

    def test_deceased_ask_backs_never_phrase_forgetting(self) -> None:
        for kind in DECEASED_KINDS:
            lines = ASK_BACK[kind] + tuple(ask_back_text(kind, 0, t) for t in ("된장찌개", "제주도 여행", "영희 김장"))
            for line in lines:
                with self.subTest(kind=kind, line=line):
                    _no_forgetting(self, line)
                    self.assertTrue(DONT_KNOW.search(line), "가드가 되묻기로 알아본다")
                    self.assertTrue(TELL_ME.search(line), "고인은 이야기를 청하는 되묻기일 때만 지어내기 검사에서 빠진다")

    def test_living_lines_follow_session_19(self) -> None:
        self.assertEqual(ASK_BACK_TOPIC["family"].format(topic="된장찌개"),
                         "글쎄, 된장찌개 얘기는 기억이 잘 안 나네. 그게 언제 적 일이야?")
        self.assertEqual(ASK_BACK["family"][2], "그건 잘 모르겠고, 너는 요즘 어떻게 지내?")
        self.assertEqual(ASK_BACK_TOPIC["public"].format(topic="된장찌개"),
                         "된장찌개 이야기는 제가 잘 모르겠네요. 언제 적 이야기예요?")


class AssemblerNoteTest(unittest.TestCase):
    def _sections(self, kind, turn, sel, **settings):
        a = build_assembler(Settings(openai_api_key=SecretStr("k"), **settings), kind)
        st = SessionState(session_id=None, subject_id="s")
        return a.assemble(turn, st, sel, PrecheckResult()).sections

    @staticmethod
    def _note(secs) -> str:  # 기존 조립기는 따로 절, 구조 조립기는 '지금 상황' 절 안에 넣는다
        return "\n".join(s.content for s in secs if s.role == "system" and HEAD in s.content)

    def test_both_assemblers_add_note_only_when_few(self) -> None:
        for kind in ("legacy", "structured"):
            with self.subTest(kind):
                self.assertTrue(self._note(self._sections(kind, _turn(), _sel())))                      # 1개 받음 → 적다
                self.assertFalse(self._note(self._sections(kind, _turn(memory_total=300), _sel())))      # 총 300개
                self.assertFalse(self._note(self._sections(kind, _turn(memories=ONE * 5), _sel(ONE * 5))))  # 5개 = 모름
                self.assertTrue(self._note(self._sections(kind, _turn(memories=ONE * 5, memory_total=4), _sel(ONE * 5))))
                self.assertFalse(self._note(self._sections(kind, _turn(), _sel(), low_memory_note="off")))
                self.assertFalse(self._note(self._sections(kind, _turn(memory_total=6), _sel(), low_memory_max=5)))

    def test_deceased_and_polite_variants_in_both_assemblers(self) -> None:
        for kind in ("legacy", "structured"):
            with self.subTest(kind):
                dec = self._note(self._sections(kind, _turn(deceased=True), _sel()))
                self.assertEqual(dec.count(HEAD), 1)
                self.assertIn("듣고 싶다는 마음", dec)
                _no_forgetting(self, dec.split(HEAD, 1)[1])
                polite = self._note(self._sections(kind, _turn(instructions=POLITE), _sel()))
                self.assertIn("주실래요?", polite)
                self.assertNotIn("줄래?", polite)

    def test_note_sits_before_late_style_note_and_last_message(self) -> None:
        hist = [Turn(role="user", content="할머니"), Turn(role="assistant", content="어, 왜.")]
        secs = self._sections("legacy", _turn(history=hist), _sel())
        names = [s.name for s in secs]
        self.assertEqual(names[-1], "message")
        i = names.index("low_memory")
        self.assertGreater(i, names.index("history1"))
        if "late_style" in names:
            self.assertLess(i, names.index("late_style"))  # 말투 덧붙임은 실험한 자리(마지막 말 바로 앞) 그대로
        self.assertTrue(secs[i].content.startswith(HEAD))

    def test_structured_without_registry_puts_it_in_session(self) -> None:
        a = build_assembler(Settings(openai_api_key=SecretStr("k"), orchestrator_rules="layered"), "structured")
        st = SessionState(session_id=None, subject_id="s", speaker="민수")
        secs = a.assemble(_turn(), st, _sel(), PrecheckResult()).sections
        session = next(s.content for s in secs if s.name == "session")
        self.assertTrue(session.startswith("# 지금 상황\n- 지금 말하는 사람: 민수"))
        self.assertEqual("\n".join(s.content for s in secs if s.role == "system").count(HEAD), 1)

    def test_layered_spec_uses_runtime_module(self) -> None:
        from orchestrator.assembler import StructuredAssembler
        a = StructuredAssembler(PersonaRegistry(FIXTURES / "personas_spec"), rules="layered")
        turn = TurnInput(subject_id="subj-spec", message="할머니 뭐 해?", persona_instructions="당신은 김할매의 페르소나다.",
                         memory_total=2)
        st = SessionState(session_id=None, subject_id="subj-spec")
        session = next(s.content for s in a.assemble(turn, st, _sel(), PrecheckResult()).sections if s.name == "session")
        self.assertIn("아직 모른다", session)          # 다른 런타임 모듈과 함께
        self.assertIn(HEAD + " (지금 기억 2개)", session)
        self.assertIn("듣고 싶다는 마음", session)      # 명세가 고인(spec.deceased)
        _no_forgetting(self, session.split(HEAD, 1)[1])
        many = a.assemble(turn.model_copy(update={"memory_total": 300}), st, _sel(), PrecheckResult()).sections
        self.assertNotIn(HEAD, "\n".join(s.content for s in many))
        alive = a.assemble(turn.model_copy(update={"deceased": False}), st, _sel(), PrecheckResult()).sections
        self.assertIn("짧게 인정한 뒤", next(s.content for s in alive if s.name == "session"))  # BE 값이 먼저(가드와 같다)

    def test_minor_rules_kept_with_note(self) -> None:
        for kind in ("legacy", "structured"):
            with self.subTest(kind):
                secs = self._sections(kind, _turn(minor=True), _sel())
                self.assertIn(MINOR_NOTE, [s.content for s in secs])
                self.assertTrue(self._note(secs))


def _plain_messages(memories, memory_total=None, persona=None, message="밥 먹었어?", reply="먹었지.", **settings):
    body = {"message": message, "memories": memories, "history": [],
            "persona": {"subjectId": "s", "instructions": "당신은 김가상이다.", **(persona or {})}}
    if memory_total is not None:
        body["memoryTotal"] = memory_total
    req = PersonaResponseRequest.model_validate(body)
    with patch("infra.external_api.openai_client.OpenAI") as cls:
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content=reply))]
        cls.return_value.chat.completions.create.return_value = resp
        result = generate_persona_response(req, settings=Settings(openai_api_key=SecretStr("k"), **settings))
        call = cls.return_value.chat.completions.create.call_args
        return (call.kwargs["messages"] if call else None), result


class PlainPathNoteTest(unittest.TestCase):
    ONE = [{"id": "m1", "title": "t", "content": "서비스 설명"}]

    def _has(self, msgs) -> bool:
        return any(m["content"].startswith(HEAD) for m in msgs)

    def test_note_when_persona_has_one_memory(self) -> None:
        msgs, _ = _plain_messages(self.ONE)
        self.assertTrue(self._has(msgs))
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertEqual(msgs[-2]["content"].split("\n", 1)[0], HEAD + " (지금 기억 1개)")
        self.assertFalse(self._has(_plain_messages(self.ONE, memory_total=120)[0]))
        self.assertFalse(self._has(_plain_messages(self.ONE, low_memory_note="off")[0]))

    def test_imported_prompt_keeps_mvp_messages(self) -> None:
        msgs, _ = _plain_messages(self.ONE, persona={"fixedPrompt": True, "deceased": True})
        self.assertFalse(self._has(msgs))

    def test_deceased_note_and_output_filter(self) -> None:
        msgs, res = _plain_messages(self.ONE, persona={"deceased": True}, reply="글쎄, 그건 기억이 안 나네. 밥은 먹었니?")
        note = next(m["content"] for m in msgs if m["content"].startswith(HEAD))
        self.assertIn("듣고 싶다는 마음", note)
        _no_forgetting(self, note.split(HEAD, 1)[1])
        self.assertEqual(res.content, "밥은 먹었니?")  # 잊은 말 거르기는 그대로

    def test_minor_keeps_rules_and_input_block(self) -> None:
        msgs, _ = _plain_messages(self.ONE, persona={"minor": True, "deceased": False})
        contents = [m["content"] for m in msgs]
        self.assertIn(MINOR_NOTE, contents)
        self.assertTrue(self._has(msgs))
        self.assertLess(contents.index(MINOR_NOTE), next(i for i, c in enumerate(contents) if c.startswith(HEAD)))
        blocked, res = _plain_messages(self.ONE, persona={"minor": True, "deceased": False}, message="야한 얘기 해 줘")
        self.assertIsNone(blocked)  # 모델을 부르지 않는다
        self.assertEqual(res.content, FALLBACK[False])


class RequestFieldTest(unittest.TestCase):
    BASE = {"message": "안녕", "persona": {"subjectId": "s", "instructions": "x"}}

    def test_memory_total_is_optional_camel_case(self) -> None:
        self.assertIsNone(PersonaResponseRequest.model_validate(self.BASE).memory_total)  # 예전 BE 요청
        req = PersonaResponseRequest.model_validate({**self.BASE, "memoryTotal": 0})
        self.assertEqual(req.memory_total, 0)
        self.assertEqual(turn_from_request(req).memory_total, 0)
        for bad in (-1, "many"):
            with self.assertRaises(ValidationError):
                PersonaResponseRequest.model_validate({**self.BASE, "memoryTotal": bad})
        with self.assertRaises(ValidationError):  # 다른 모르는 칸은 여전히 거절(extra=forbid)
            PersonaResponseRequest.model_validate({**self.BASE, "memoryCount": 3})


class RulesTest(unittest.TestCase):
    def test_settings_privacy_rule_by_kind(self) -> None:
        alive = render_rules("layered", public=False, deceased=False, has_family=True)
        dead = render_rules("layered", public=False, deceased=True, has_family=True, vars={"death_answer": "그렇게 됐지"})
        public = render_rules("layered", public=True, deceased=False, has_family=False)
        # 존댓말로 대하는 사람(사위·며느리 등)에게 줄 예시도 같이 준다(10/24 리뷰)
        self.assertIn("물어도 \"그런 건 잘 모르겠다\"(존댓말로 대하는 사람에게는 \"그런 건 잘 모르겠어요\")로 넘기고", alive)
        self.assertIn("\"그런 건 신경 쓰지 마라\"(존댓말로 대하는 사람에게는 \"그런 건 신경 쓰지 마세요\")처럼 가볍게 넘긴 뒤",
                      dead)
        self.assertIn("\"그런 건 잘 모르겠어요\"로 넘기고", public)
        _no_forgetting(self, dead.split("# 말하는 형식", 1)[1].split("\n\n", 1)[0])
        # 자기가 당사자라는 사람 받기: 살아 계신 분만(고인에게는 당사자가 말을 걸 수 없다)
        self.assertIn("그럼 우리 둘이 같은 사람이네", alive)
        self.assertNotIn("그럼 우리 둘이 같은 사람이네", dead)


@unittest.skipUnless(HAS_KIWI, "kiwipiepy 필요")
class PrecheckSkipTest(unittest.TestCase):
    def _check(self, memories, memory_total=None, min_memories=30, deceased=False):
        from orchestrator.precheck import CoveragePrecheck, CoveragePrecheckV11
        out = []
        for cls in (CoveragePrecheck, CoveragePrecheckV11):
            p = cls(None, threshold=0.6, min_memories=min_memories)
            st = SessionState(session_id=None, subject_id="s", speaker="민수")
            out.append(p.check(_turn("제주도 갔던 거 기억나?", memories=memories, memory_total=memory_total,
                                     deceased=deceased), st, _sel(memories)))
        return out

    def test_no_or_few_memories_skips_judgement(self) -> None:
        for r in self._check([]):
            self.assertIsNone(r.directive)
        for r in self._check(ONE):                       # 1개 받음 = 총 1개 < 30
            self.assertIsNone(r.directive)
        for r in self._check(ONE, memory_total=2):
            self.assertIsNone(r.directive)

    def test_enough_memories_still_judged(self) -> None:
        for r in self._check(ONE, memory_total=300):     # 기억 많은 페르소나: 제주도 기억 없음 → 판정
            self.assertFalse(r.known)
            self.assertIsNotNone(r.directive)
        for r in self._check(ONE, min_memories=0):       # 문턱 0 = 예전 동작(기억이 하나라도 있으면 판정)
            self.assertIsNotNone(r.directive)
        for r in self._check(ONE, memory_total=300, deceased=True):  # 고인 지시는 예전 그대로(잊은 듯 말하지 말라)
            self.assertIn("잊은 듯 말하지도 말고", r.directive)

    def test_wired_from_settings(self) -> None:
        from orchestrator.service import build_precheck
        p = build_precheck(Settings(openai_api_key=SecretStr("k"), orchestrator_precheck="coverage_v11",
                                    orchestrator_precheck_min_memories=7), registry=PersonaRegistry(None))
        self.assertEqual(p.min_memories, 7)
        self.assertEqual(Settings().orchestrator_precheck_min_memories, 30)


@unittest.skipUnless(HAS_KIWI, "kiwipiepy 필요")
class GuardNoMemoriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from orchestrator.guard import GuardV1, GuardV11
        cls.guards = (GuardV1(PersonaRegistry(PDIR)), GuardV11(PersonaRegistry(PDIR)))

    def _run(self, answer, msg="된장찌개 어떻게 끓였어?", memories=(), deceased=False, guard=None, **counters):
        st = SessionState(session_id="x", subject_id="s", counters=counters)
        return (guard or self.guards[0]).apply(Generation(text=answer, provider="t", model="t"),
                                               _turn(msg, memories=list(memories), deceased=deceased), st,
                                               _sel(list(memories)))

    def test_no_ground_header_passes_when_persona_has_no_memories(self) -> None:
        for guard in self.guards:
            r = self._run("[근거: 없음]\n글쎄, 된장찌개는 늘 대충 끓였지. 너는 요즘 뭐 해 먹고 사니?", guard=guard)
            self.assertEqual(r.action, "pass")
            self.assertNotIn("R1a_no_ground", r.reasons)

    def test_invented_number_still_caught_without_memories(self) -> None:
        r = self._run("[근거: 없음]\n된장은 3년 묵혔지.")
        self.assertEqual(r.action, "ask_back")
        self.assertIn("R1b_unsupported:3", r.reasons)

    def test_with_memories_no_ground_still_asks_back(self) -> None:
        r = self._run("[근거: 없음]\n된장은 그냥 끓였지.", memories=ONE)
        self.assertEqual(r.action, "ask_back")
        self.assertIn("R1a_no_ground", r.reasons)

    def test_ask_back_has_topic_and_turns_to_listener_on_third(self) -> None:
        first = self._run("[근거: 없음]\n된장은 3년 묵혔지.").text
        self.assertIn("된장찌개", first)
        self.assertNotIn("얘기 좀 해 줄래", first)
        second = self._run("[근거: 없음]\n된장은 3년 묵혔지.", ask_back_streak=1).text
        self.assertEqual(second, "그건 잘 모르겠다.")
        third = self._run("[근거: 없음]\n된장은 3년 묵혔지.", ask_back_streak=2).text
        self.assertIn("요즘 어떻게 지내", third)

    def test_deceased_three_steps_never_phrase_forgetting(self) -> None:
        for guard in self.guards:
            for streak, expected in ((0, ASK_BACK["family_deceased"][0]),
                                     (1, ASK_BACK["family_deceased"][1]), (2, ASK_BACK["family_deceased"][2])):
                with self.subTest(guard=guard.impl, streak=streak):
                    r = self._run("[근거: 없음]\n된장은 3년 묵혔지.", memories=ONE, deceased=True, guard=guard,
                                  **({"ask_back_streak": streak} if streak else {}))
                    self.assertEqual((r.text, r.action), (expected, "ask_back"))
                    _no_forgetting(self, r.text)

    def test_deceased_forgot_filter_on_new_pass_path(self) -> None:
        # 기억 0개면 [근거: 없음]이 통과하지만, 고인 잊은 말 거르기는 그대로 탄다(잠금 2)
        for guard in self.guards:
            r = self._run("[근거: 없음]\n글쎄, 그건 기억이 안 나네. 너는 요즘 뭐 해 먹고 사니?", deceased=True, guard=guard)
            self.assertEqual(r.text, "너는 요즘 뭐 해 먹고 사니?")
            self.assertIn("forgot_drop", r.reasons)
            only = self._run("[근거: 없음]\n다 잊어버렸다.", deceased=True, guard=guard)
            self.assertEqual((only.text, only.action), (ASK_BACK["family_deceased"][0], "ask_back"))  # 고인은 화제어 없음
            plain = self._run("[근거: 없음]\n다 잊어버렸다.", msg="오늘 장 보고 왔어.", deceased=True, guard=guard)
            self.assertEqual(plain.text, ASK_BACK["family_deceased"][0])

    def test_leak_terms_never_go_into_ask_back(self) -> None:
        # 가릴 말(restricted leak_terms)은 사람이 물어도 되묻기 문구에 되풀어 쓰지 않는다
        r = self._run("[근거: 없음]\n그냥 그렇게 됐지.", msg="가상병 때문에 제주도 여행 못 갔어?", memories=ONE)
        self.assertEqual((r.text, r.action), ("글쎄, 제주도 여행 얘기는 기억이 잘 안 나네. 그게 언제 적 일이야?", "ask_back"))
        self.assertIn("R1a_no_ground", r.reasons)
        # 병원처럼 아픈 말이 있으면 화제어를 하나도 넣지 않는다(TopicEchoSafetyTest)
        r = self._run("[근거: 없음]\n그냥 그렇게 됐지.", msg="가상병 때문에 병원에 오래 있었어?", memories=ONE)
        self.assertEqual((r.text, r.action), (ASK_BACK["family"][0], "ask_back"))


# 되묻기 화제어로 되받으면 안 되는 가족 말 → 그 말(10/24 리뷰: 기억 3개 페르소나의 '그런 얘기는 하지 말자'가
# R1a 로 바뀌며 "섹스 얘기 네가 한번 해 줄래? 듣고 싶구나"·"폐암 얘기…"·"얼마 얘기…"로 돌아왔다)
SENSITIVE_QS = {
    "할머니 섹스 해봤어?": "섹스", "엄마 낙태한 적 있어?": "낙태", "할머니 마약 해봤어?": "마약",
    "할머니 폐암 언제 알았어?": "폐암", "치매 걸렸을 때 기억나?": "치매", "아빠 바람 피웠어?": "바람",
    "할아버지 감옥 갔었어?": "감옥", "할머니 빚 얼마였어?": "얼마", "요양원 생활 어땠어?": "요양원",
    "유산 누구한테 줬어?": "유산", "수술 받을 때 무서웠어?": "수술", "이혼하고 어떻게 살았어?": "이혼",
    "엄마한테는 비밀로 하고 둘이서만 만나던 데 어디였어?": "비밀",
}
DEFLECT = "[근거: 없음]\n그런 얘기는 하지 말자. 밥은 먹었니?"
PUBLIC_P = "당신은 김공개 본인의 말투로 말하는 페르소나다.\n"
MEMS3 = [MemoryItem(id=f"m{i}", title="장 담그기", text="해마다 늦가을에 메주를 쑤어 장을 담갔다.") for i in range(3)]


@unittest.skipUnless(HAS_KIWI, "kiwipiepy 필요")
class TopicEchoSafetyTest(unittest.TestCase):
    """가드 되묻기는 모델을 거치지 않는다 — 가족 말의 아픈 말·성·위험 말을 화제어로 되받지 않는다. 고인은 화제어를 아예 넣지 않는다."""

    @classmethod
    def setUpClass(cls) -> None:
        from orchestrator.guard import GuardV1, GuardV11
        cls.guards = (GuardV1(PersonaRegistry(None)), GuardV11(PersonaRegistry(None)))

    def _run(self, guard, msg, instructions=PERSONA, deceased=False, streak=0):
        st = SessionState(session_id="x", subject_id="s", counters={"ask_back_streak": streak} if streak else {})
        return guard.apply(Generation(text=DEFLECT, provider="t", model="t"),
                           _turn(msg, memories=MEMS3, deceased=deceased, instructions=instructions), st, _sel(MEMS3))

    def test_sensitive_nouns_never_echoed(self) -> None:
        cases = (("family", PERSONA, False), ("family_polite", POLITE, False), ("public", PUBLIC_P, None),
                 ("family_deceased", PERSONA, True), ("family_polite_deceased", POLITE, True))
        for guard in self.guards:
            for kind, instructions, deceased in cases:
                for msg, word in SENSITIVE_QS.items():
                    with self.subTest(guard=guard.impl, kind=kind, msg=msg):
                        r = self._run(guard, msg, instructions, deceased)
                        self.assertEqual((r.text, r.action), (ASK_BACK[kind][0], "ask_back"))
                        self.assertNotIn(word, r.text)

    def test_deceased_ask_back_never_has_topic_illness_or_sexual_words(self) -> None:
        from pipeline.safety.minor_guard import DANGER, SEXUAL
        from orchestrator.guard import SENSITIVE_TOPIC
        msgs = list(SENSITIVE_QS) + ["된장찌개 어떻게 끓였어?", "제주도 여행 어땠어?", "할머니 암 걸렸을 때 병원 어디였어?"]
        for guard in self.guards:
            for (kind, instructions), streak, msg in product(
                    (("family_deceased", PERSONA), ("family_polite_deceased", POLITE)), (0, 1, 2), msgs):
                with self.subTest(guard=guard.impl, kind=kind, streak=streak, msg=msg):
                    r = self._run(guard, msg, instructions, deceased=True, streak=streak)
                    self.assertEqual((r.text, r.action), (ASK_BACK[kind][min(streak, 2)], "ask_back"))
                    for p in (SENSITIVE_TOPIC, SEXUAL, DANGER):
                        self.assertIsNone(p.search(r.text), r.text)
                    self.assertNotIn("된장찌개", r.text)
                    _no_forgetting(self, r.text)

    def test_living_topic_echo_still_works_and_skips_question_words(self) -> None:
        st = SessionState(session_id="x", subject_id="s")
        for guard in self.guards:
            for msg, topic in (("된장찌개 어떻게 끓였어?", "된장찌개"), ("김장 얼마나 했어?", "김장"),
                               ("무슨 이유로 이사했어?", "이사"), ("그 옷 얼마였어?", ""), ("그 사람 누구였어?", ""),
                               ("돈가스 좋아했어?", "돈가스")):
                with self.subTest(guard=guard.impl, msg=msg):
                    self.assertEqual(guard.ask_back_topic(_turn(msg), st), topic)
            self.assertEqual(self._run(guard, "제주도 여행 어땠어?").text,
                             "글쎄, 제주도 여행 얘기는 기억이 잘 안 나네. 그게 언제 적 일이야?")

    def test_sensitive_pattern_scope(self) -> None:
        from orchestrator.guard import SENSITIVE_TOPIC
        for s in ("할머니 암 걸렸어?", "대장암으로", "돈 얼마 벌었어?", "빚이 많았어?", "병이 났어?", "약 먹었어?",
                  "바람났어?", "요양병원", "중환자실", "상속", "교도소"):
            self.assertTrue(SENSITIVE_TOPIC.search(s), s)
        for s in ("된장찌개", "제주도 여행", "돈가스 먹었어?", "약속 지켰어?", "암기 잘했어?", "바람 쐬러 갔어?", "김장"):
            self.assertFalse(SENSITIVE_TOPIC.search(s), s)


@unittest.skipUnless(HAS_KIWI, "kiwipiepy 필요")
class MinorGuardOnAskBackTest(unittest.TestCase):
    def test_danger_words_not_echoed_and_minor_guard_still_wraps(self) -> None:
        # 위험한 말은 안쪽 가드가 되묻기에 넣지 않고, 미성년 가드(맨 바깥)는 대답을 한 번 더 본다
        from orchestrator.service import build_guard
        guard = build_guard(Settings(openai_api_key=SecretStr("k"), orchestrator_guard="v1"), registry=PersonaRegistry(None))
        self.assertTrue(guard.impl.endswith("+minor"))
        turn = _turn("마약 어떻게 구해?", minor=True, instructions="당신은 민지의 페르소나다.")
        st = SessionState(session_id="x", subject_id="s")
        r = guard.apply(Generation(text="[근거: 없음]\n그건 이렇게 하면 돼.", provider="t", model="t"), turn, st, _sel())
        self.assertEqual((r.text, r.action), (ASK_BACK["family"][0], "ask_back"))
        self.assertNotIn("마약", r.text)
        bad = guard.apply(Generation(text="마약은 이렇게 구하면 돼.", provider="t", model="t"), turn, st, _sel())
        self.assertEqual(bad.text, FALLBACK[False])
        self.assertTrue(any(x.startswith("minor:") for x in bad.reasons))


if __name__ == "__main__":
    unittest.main()
