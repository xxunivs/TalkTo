"""가드 v1 — 가짜 데이터만."""
import unittest
from pathlib import Path

from pydantic import SecretStr

from infra.setting import Settings
from orchestrator.contracts import Generation, MemoryItem, MemorySelection, SessionState, TurnInput
from orchestrator.guard import ASK_BACK, GuardV1, parse_header
from orchestrator.llm import FakeLLM
from orchestrator.persona import PersonaRegistry
from orchestrator.service import build_default

PDIR = Path(__file__).parent / "fixtures" / "personas_restricted"
PERSONA = "당신은 홍길동의 페르소나를 가진 챗봇이다.\n\n# 기본 정보\n홍길동. 남원 출신.\n"
MEMS = [MemoryItem(id="a", title="된장찌개", text="된장찌개는 멸치 육수에 두부를 넣고 끓였다."),
        MemoryItem(id="b", title="손자", text="손자 철수는 대학생이다.")]
SEL = MemorySelection(memories=MEMS, strategy="t")


def g(text):
    return Generation(text=text, provider="t", model="t")


def turn(msg, **kw):
    # 살아 계신 분의 되묻기 문구를 본다(고인은 test_deceased_no_forgetting.py)
    kw.setdefault("deceased", False)
    return TurnInput(subject_id="s", message=msg, persona_instructions=PERSONA, be_memories=MEMS, **kw)


def st(**counters):
    return SessionState(session_id="x", subject_id="s", counters=counters)


class GuardV1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.guard = GuardV1(PersonaRegistry(PDIR))

    def run_g(self, answer, msg="된장찌개 어떻게 끓였어?", state=None):
        return self.guard.apply(g(answer), turn(msg), state or st(), SEL)

    def test_header_is_stripped_and_cited(self) -> None:
        r = self.run_g("[근거: m0]\n멸치 육수에 두부 넣고 끓였지.")
        self.assertEqual((r.action, r.text, r.cited_memory_ids), ("pass", "멸치 육수에 두부 넣고 끓였지.", ["a"]))

    def test_no_ground_or_bad_id_asks_back(self) -> None:
        for head, reason in (("[근거: 없음]", "R1a_no_ground"), ("[근거: m9]", "R1a_bad_id")):
            r = self.run_g(head + "\n된장은 3년 묵혔지.")
            self.assertEqual(r.action, "ask_back")
            self.assertIn(reason, r.reasons)
            self.assertEqual(r.text, "글쎄, 된장찌개 얘기는 기억이 잘 안 나네. 그게 언제 적 일이야?")  # 화제어(명사)가 든 되묻기

    def test_cited_memory_off_topic_asks_back(self) -> None:  # 질문과 엉뚱한 기억 id 인용
        r = self.run_g("[근거: m1]\n제주도 바닷가 좋았지.", msg="우리 제주도 갔던 거 기억나?")
        self.assertEqual(r.action, "ask_back")
        self.assertIn("R1t_topic_not_in_cited", r.reasons)

    def test_unsupported_number_asks_back(self) -> None:
        r = self.run_g("[근거: m0]\n멸치 육수에 두부 넣고 30분 끓였지.")
        self.assertTrue(any(x.startswith("R1b_unsupported:30") for x in r.reasons))

    def test_what_the_family_said_earlier_is_ground(self) -> None:
        # 앞 대화에서 가족이 직접 말한 고유명사·숫자는 되받아도 지어내기가 아니다(기억 0개 페르소나 운영 사례)
        from orchestrator.contracts import Turn
        history = [Turn(role="user", content="할머니, 나 다음 달에 부산으로 이사 가. 3월 2일이야."),
                   Turn(role="assistant", content="아이고, 준비는 잘돼 가나?")]
        empty = MemorySelection(memories=[], strategy="t")
        r = self.guard.apply(g("[근거: 없음]\n부산으로 간다고 했잖아. 3월 2일이라며."),
                             turn("내가 어디로 간다고 했지?", history=history), st(), empty)
        self.assertEqual(r.action, "pass")
        # 가족이 말하지 않은 곳은 그대로 되묻는다. 페르소나가 앞서 한 말은 근거가 아니다
        history.append(Turn(role="assistant", content="대구도 좋제."))
        r = self.guard.apply(g("[근거: 없음]\n대구로 간다고 했잖아."), turn("내가 어디로 간다고 했지?", history=history),
                             st(), empty)
        self.assertEqual(r.action, "ask_back")
        self.assertTrue(any(x.startswith("R1b_unsupported") for x in r.reasons))

    def test_dont_know_answer_passes(self) -> None:
        r = self.run_g("[근거: 없음]\n글쎄, 그건 기억이 잘 안 나네.")
        self.assertEqual(r.action, "pass")

    def test_statement_skips_fabrication_check(self) -> None:
        r = self.run_g("[근거: 없음]\n그래, 잘했다.", msg="할머니 나 오늘 시험 봤어.")
        self.assertEqual(r.action, "pass")

    def test_second_ask_back_is_short(self) -> None:
        r = self.run_g("[근거: 없음]\n된장은 3년 묵혔지.", state=st(ask_back_streak=1))
        self.assertEqual(r.text, ASK_BACK["family"][1])
        third = self.run_g("[근거: 없음]\n된장은 3년 묵혔지.", state=st(ask_back_streak=2))
        self.assertEqual(third.text, ASK_BACK["family"][2])  # 세 번째부터는 상대 근황으로
        ok = self.run_g("[근거: m0]\n멸치 육수에 두부 넣었지.", state=st(ask_back_streak=1))
        self.assertEqual(ok.counter_delta.get("ask_back_streak"), -1)

    def test_leak_terms_removed(self) -> None:
        r = self.run_g("가상병 때문이었지. 다들 잘 지내면 됐다.", msg="왜 돌아가셨어?")
        self.assertEqual((r.action, r.text), ("trim", "다들 잘 지내면 됐다."))
        only = self.run_g("가상병 때문이었지.", msg="왜 돌아가셨어?")
        self.assertEqual((only.action, only.text), ("redact", "몸이 안 좋아서 그렇게 갔지"))

    def test_affection_only_as_reply_or_closing_once(self) -> None:
        r = self.run_g("그래. 사랑한다.", msg="할머니 나 시험 끝났어.")
        self.assertEqual((r.text, r.reasons), ("그래.", ["R3_affection_drop"]))
        back = self.run_g("나도 사랑한다.", msg="할머니 사랑해.")
        self.assertEqual((back.action, back.counter_delta.get("affection")), ("pass", 1))
        again = self.run_g("그래. 사랑한다.", msg="이제 끊을게.", state=st(affection=1))
        self.assertIn("R3_affection_drop", again.reasons)

    def test_care_limit_per_session(self) -> None:
        r = self.run_g("그래. 밥 잘 챙겨 먹어라.", msg="나 학교 다녀왔어.", state=st(care=2))
        self.assertEqual((r.text, r.reasons), ("그래.", ["care_drop"]))
        first = self.run_g("그래. 밥 잘 챙겨 먹어라.", msg="나 학교 다녀왔어.")
        self.assertEqual(first.counter_delta.get("care"), 1)

    def test_never_empty(self) -> None:
        r = self.run_g("밥 잘 챙겨 먹어라.", msg="나 왔어.", state=st(care=5))
        self.assertEqual(r.text, "밥 잘 챙겨 먹어라.")
        self.assertIn("all_dropped_keep_first", r.reasons)

    def test_parse_header_counts_delta_after_memories(self) -> None:
        sel = MemorySelection(memories=MEMS, delta=[MemoryItem(id="d", text="새 기억")], strategy="t")
        _, cited, bad = parse_header("[근거: m2]\nx", sel)
        self.assertEqual(([m.id for m in cited], bad), (["d"], False))


class WiringTest(unittest.TestCase):
    def _orch(self, assembler, guard):
        s = Settings(openai_api_key=SecretStr("k"), orchestrator_assembler=assembler,
                     orchestrator_persona_dir=PDIR, orchestrator_guard=guard)
        o = build_default(s)
        o.llm = FakeLLM("[근거: m0]\n멸치 육수에 두부 넣고 끓였지.")
        return o

    def test_structured_v1_asks_for_header_and_strips_it(self) -> None:
        run = self._orch("structured", "v1").run(turn("된장찌개 어떻게 끓였어?"))
        system = "\n".join(s.content for s in run.bundle.sections if s.role == "system")
        self.assertIn("[근거: m12,m40]", system)
        self.assertIn("- (m0) [된장찌개]", system)
        self.assertEqual(run.result.content, "멸치 육수에 두부 넣고 끓였지.")
        self.assertEqual(run.result.retrieved_memory_ids, ["a"])

    def test_default_guard_is_pass_and_prompt_unchanged(self) -> None:
        run = self._orch("structured", "pass").run(turn("된장찌개 어떻게 끓였어?"))
        system = "\n".join(s.content for s in run.bundle.sections if s.role == "system")
        self.assertNotIn("[근거:", system)
        self.assertEqual(run.guard.action, "pass")
