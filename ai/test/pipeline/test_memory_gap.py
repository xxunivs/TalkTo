"""기억 빈틈 표시(flagGaps → memoryGap)·가족이 고쳐 준 말투(styleExamples)·질문 만들기(/v1/persona/gap-question).

실제 API 호출 없음. 기존 경로와 중간계층(legacy·structured)이 같은 자리에 같은 절을 넣고, 표시는 음성 표시·가드·
대화 기록보다 먼저 떼어 낸다. 요청에 없으면 예전과 한 글자도 다르지 않다.
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from infra.schema.persona import GapQuestionRequest, PersonaResponseRequest, StyleExample
from infra.setting import Settings, get_settings
from main import app
from orchestrator.contracts import GuardResult, PrecheckResult, StyleFix, TurnInput
from orchestrator.guard import PassGuard
from orchestrator.llm import FakeLLM
from orchestrator.persona import PersonaRegistry
from orchestrator.service import (
    build_assembler,
    build_default,
    orchestrate_persona_response,
    reset_orchestrator,
)
from pipeline.s3_synthesis.c_build_system_prompt import STYLE_LATE_NOTE, generate_persona_response
from pipeline.s3_synthesis.e_memory_gap import (
    GAP_INSTRUCTION,
    STYLE_HEAD,
    gap_blocked,
    make_gap_question,
    split_gap,
    style_block,
)
from test.pipeline.test_late_style_note import WITH

FISH = "fish_0123abcd"
STYLE = [{"original": "그래, 밥은 먹었니? 요즘 날이 춥구나.", "corrected": "밥 먹었냐."}]
REQUEST = {
    "message": "할머니가 즐겨 부르시던 노래 기억나요?",
    "history": [{"role": "user", "content": "안녕하세요"}, {"role": "assistant", "content": "어, 왔니"}],
    "memories": [{"id": "m1", "title": "고향", "content": "홍길동은 남원에서 태어났다."}],
    "persona": {"subjectId": "subj-1", "instructions": "당신은 홍길동이다."},
}


def _settings(**kw) -> Settings:
    return Settings(openai_api_key=SecretStr("sk-test"), **kw)


def _mock_openai(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=3)
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


def _legacy(body: dict, reply: str, **settings_kw):
    req = PersonaResponseRequest.model_validate(body)
    with patch("infra.external_api.openai_client.OpenAI") as cls:
        cls.return_value = _mock_openai(reply)
        result = generate_persona_response(req, settings=_settings(**settings_kw))
        messages = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
    return result, messages


class SplitGapTest(unittest.TestCase):
    def test_no_mark_leaves_text_untouched(self) -> None:
        for text in ("어, 왔니.", "[감정:warm] 그래.", "  공백 그대로  ", ""):
            self.assertEqual(split_gap(text), (None, text))

    def test_mark_variants_are_removed_with_topic(self) -> None:
        cases = {
            "글쎄, 노래는 많이 불렀지. [기억없음:즐겨 부른 노래]": ("즐겨 부른 노래", "글쎄, 노래는 많이 불렀지."),
            "그랬지 [기억 없음 : 고향 친구]": ("고향 친구", "그랬지"),
            "(기억없음：시장) 그래 시장에 갔지.": ("시장", "그래 시장에 갔지."),
            "응 그랬지.【기억없음】": ("", "응 그랬지."),
            "[기억없음:노래] 노래? [기억없음:가수]": ("노래", "노래?"),
        }
        for raw, expected in cases.items():
            self.assertEqual(split_gap(raw), expected, raw)

    def test_other_brackets_stay(self) -> None:
        topic, text = split_gap("[근거: m1]\n남원이지. [기억없음:학교]")
        self.assertEqual(topic, "학교")
        self.assertEqual(text, "[근거: m1]\n남원이지.")

    def test_odd_shaped_marks_never_leak(self) -> None:
        # 엄격한 모양(쌍점 + 괄호 없는 40자 이하 주제)이 아니어도 여는 괄호 + 기억없음이면 뗀다(GF-1)
        long_topic = "할머니가 시집오시기 전에 고향 마을 냇가에서 동무들과 함께 부르시던 노래 제목"
        self.assertGreater(len(long_topic), 40)
        cases = {
            "그랬지 [기억없음 노래]": ("노래", "그랬지"),
            "그랬지 [기억없음 - 노래]": ("노래", "그랬지"),
            "그랬지 [기억없음/노래]": ("노래", "그랬지"),
            "그랬지 [기억없음:노래(제목)]": ("노래(제목)", "그랬지"),
            "그랬지 (기억없음:노래(제목))": ("노래(제목)", "그랬지"),
            f"그랬지 [기억없음:{long_topic}]": (long_topic, "그랬지"),
            "그랬지 [기억없음:노래": ("노래", "그랬지"),  # 닫는 괄호 없음 → 줄 끝까지
            "그랬지 [기억없음:노래\n또 와라.": ("노래", "그랬지\n또 와라."),
            "[기억없음 - 노래] 글쎄다.": ("노래", "글쎄다."),  # 닫혔으면 뒤 대답은 남긴다
            "[기억없음:노래] 노래? [기억없음 - 가수]": ("노래", "노래?"),
            "그래 [기억없음 - 노래 (기억없음:가수)] 응": ("노래", "그래 응"),
        }
        for raw, expected in cases.items():
            self.assertEqual(split_gap(raw), expected, raw)
            self.assertNotIn("기억", expected[1])

    def test_loose_topic_is_capped_at_60(self) -> None:
        topic, text = split_gap("x [기억없음:" + "가" * 80)
        self.assertEqual((topic, text), ("가" * 60, "x"))


class PlainPathTest(unittest.TestCase):
    def test_without_flags_messages_and_json_are_unchanged(self) -> None:
        result, messages = _legacy(REQUEST, "노래? 많이 불렀지. [기억없음:노래]")
        self.assertFalse(any(GAP_INSTRUCTION == m["content"] for m in messages))
        self.assertFalse(any(STYLE_HEAD in m["content"] for m in messages))
        # 요청하지 않았는데 붙여도 화면 글에는 남지 않고, 응답에 memoryGap 칸도 없다
        self.assertEqual(result.content, "노래? 많이 불렀지.")
        self.assertNotIn("memoryGap", result.model_dump(by_alias=True))

    def test_flag_gaps_adds_instruction_and_returns_topic(self) -> None:
        body = dict(REQUEST, flagGaps=True, styleExamples=STYLE)
        result, messages = _legacy(body, "노래? 많이 불렀지. [기억없음:즐겨 부른 노래]")
        self.assertEqual(messages[0]["content"], "당신은 홍길동이다.")
        self.assertEqual(messages[1], {"role": "system", "content": GAP_INSTRUCTION})
        self.assertTrue(messages[2]["content"].startswith(STYLE_HEAD))
        self.assertIn("밥 먹었냐.", messages[2]["content"])
        self.assertIn("남원", messages[3]["content"])  # 참고 기억은 그 뒤
        self.assertEqual(messages[-1], {"role": "user", "content": REQUEST["message"]})
        self.assertEqual(result.content, "노래? 많이 불렀지.")
        self.assertEqual(result.model_dump(by_alias=True)["memoryGap"], {"topic": "즐겨 부른 노래"})

    def test_odd_shaped_mark_never_reaches_content_or_speech(self) -> None:
        # FISH_EMOTION=off(지금 운영): content 가 그대로 화면·대화 기록·목소리로 간다
        for reply in ("노래? 많이 불렀지. [기억없음 - 즐겨 부른 노래]", "노래? 많이 불렀지. [기억없음:노래(제목)]",
                      "노래? 많이 불렀지. [기억없음:즐겨 부른 노래"):
            result, _ = _legacy(dict(REQUEST, flagGaps=True), reply, fish_emotion="off")
            self.assertEqual(result.content, "노래? 많이 불렀지.", reply)
            self.assertIsNone(result.speech_text)
            self.assertTrue(result.memory_gap.topic)
            self.assertNotIn("기억없음", json.dumps(result.model_dump(by_alias=True), ensure_ascii=False))

    def test_no_gap_returns_no_memory_gap(self) -> None:
        result, _ = _legacy(dict(REQUEST, flagGaps=True), "남원이지.")
        self.assertIsNone(result.memory_gap)
        self.assertNotIn("memoryGap", result.model_dump(by_alias=True))

    def test_gap_is_split_before_fish_cues(self) -> None:
        body = dict(REQUEST, flagGaps=True, persona=dict(REQUEST["persona"], voiceId=FISH))
        result, messages = _legacy(body, "[감정:warm] 노래야 많이 불렀지. [기억없음:노래]", fish_emotion="cue")
        self.assertEqual(messages[2], {"role": "system", "content": GAP_INSTRUCTION})  # 음성 지침 바로 뒤
        self.assertEqual(result.content, "노래야 많이 불렀지.")
        self.assertEqual(result.emotion, "warm")
        self.assertEqual(result.memory_gap.topic, "노래")

    def test_fixed_prompt_gets_both_sections_when_sent(self) -> None:
        body = dict(REQUEST, flagGaps=True, styleExamples=STYLE,
                    persona=dict(REQUEST["persona"], fixedPrompt=True))
        _, messages = _legacy(body, "그래.")
        self.assertEqual(messages[1]["content"], GAP_INSTRUCTION)
        self.assertTrue(messages[2]["content"].startswith(STYLE_HEAD))

    def test_style_examples_keep_the_late_style_note_last(self) -> None:
        body = dict(REQUEST, styleExamples=STYLE, persona=dict(REQUEST["persona"], instructions=WITH))
        _, messages = _legacy(body, "그래.")
        self.assertEqual(messages[-2], {"role": "system", "content": STYLE_LATE_NOTE})
        self.assertTrue(messages[1]["content"].startswith(STYLE_HEAD))

    def test_style_examples_are_capped_and_validated(self) -> None:
        with self.assertRaises(ValidationError):
            PersonaResponseRequest.model_validate(dict(REQUEST, styleExamples=STYLE * 11))
        with self.assertRaises(ValidationError):
            PersonaResponseRequest.model_validate(
                dict(REQUEST, styleExamples=[{"original": "a", "corrected": "x" * 301}]))
        block = style_block([StyleExample(original="긴 " * 200, corrected="응.")])
        self.assertIn("말 높이(존댓말·반말)와 위 규칙은 그대로 지킨다", block)
        self.assertLess(len(block), 700)


class OrchestratorTest(unittest.TestCase):
    def test_legacy_orchestrator_matches_plain_path(self) -> None:
        body = dict(REQUEST, flagGaps=True, styleExamples=STYLE)
        reply = "노래? 많이 불렀지. [기억없음:노래]"
        plain, plain_messages = _legacy(body, reply)
        settings = _settings()
        with patch("infra.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai(reply)
            orch = orchestrate_persona_response(PersonaResponseRequest.model_validate(body), settings=settings,
                                                orchestrator=build_default(settings))
            orch_messages = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(orch_messages, plain_messages)
        self.assertEqual(orch.model_dump(), plain.model_dump())

    def test_guard_and_session_never_see_the_mark(self) -> None:
        seen: list[str] = []

        class Spy(PassGuard):
            def apply(self, generation, turn, state, selection) -> GuardResult:
                seen.append(generation.text)
                return super().apply(generation, turn, state, selection)

        orch = build_default(_settings(fish_emotion="inline"))
        orch.llm = FakeLLM("노래야 [미소] 많이 불렀지. [기억없음:노래]")
        orch.guard = Spy()
        turn = TurnInput(subject_id="s", message="무슨 노래 좋아하셨어요?", persona_instructions="p",
                         voice_id=FISH, session_id="gap-1", flag_gaps=True)
        run = orch.run(turn)
        self.assertEqual(seen, ["노래야 많이 불렀지."])
        self.assertEqual(run.result.content, "노래야 많이 불렀지.")
        self.assertEqual(run.result.memory_gap, "노래")
        self.assertNotIn("기억없음", json.dumps(run.next_state.model_dump(), ensure_ascii=False))

    def test_odd_shaped_mark_never_reaches_guard_or_session(self) -> None:
        # 음성 표시 off(지금 운영)에서도 모양이 어긋난 표시가 가드·세션·대답에 가지 않는다(GF-1)
        seen: list[str] = []

        class Spy(PassGuard):
            def apply(self, generation, turn, state, selection) -> GuardResult:
                seen.append(generation.text)
                return super().apply(generation, turn, state, selection)

        for i, raw in enumerate(("노래야 많이 불렀지. [기억없음 - 노래]", "노래야 많이 불렀지. [기억없음:노래(제목)]")):
            seen.clear()
            orch = build_default(_settings(fish_emotion="off"))
            orch.llm = FakeLLM(raw)
            orch.guard = Spy()
            turn = TurnInput(subject_id="s", message="무슨 노래 좋아하셨어요?", persona_instructions="p",
                             session_id=f"gap-odd-{i}", flag_gaps=True)
            run = orch.run(turn)
            self.assertEqual(seen, ["노래야 많이 불렀지."], raw)
            self.assertEqual(run.result.content, "노래야 많이 불렀지.")
            self.assertTrue(run.result.memory_gap.startswith("노래"))
            self.assertNotIn("기억없음", json.dumps(run.next_state.model_dump(), ensure_ascii=False))

    def test_precheck_unknown_counts_as_gap_only_when_asked(self) -> None:
        class Unknown:
            impl = "unknown"

            def check(self, turn, state, selection) -> PrecheckResult:
                return PrecheckResult(known=False, coverage=0.0, topic_terms=["노래", "가수"], directive="모른다")

        orch = build_default(_settings())
        orch.llm = FakeLLM("그래.")
        orch.precheck = Unknown()
        base = dict(subject_id="s", message="무슨 노래?", persona_instructions="p")
        self.assertEqual(orch.run(TurnInput(flag_gaps=True, **base)).result.memory_gap, "노래·가수")
        self.assertIsNone(orch.run(TurnInput(**base)).result.memory_gap)

    def test_structured_puts_sections_after_voice_and_before_memories(self) -> None:
        asm = build_assembler(_settings(orchestrator_assembler="structured", fish_emotion="cue"), "structured",
                              registry=PersonaRegistry(None))
        orch = build_default(_settings())
        turn = TurnInput(subject_id="s", message="노래?", persona_instructions=WITH, voice_id=FISH, flag_gaps=True,
                         style_examples=[StyleFix(**STYLE[0])], be_memories=[])
        run = orch.run(turn, until="select")
        bundle = asm.assemble(turn, run.state, run.selection, PrecheckResult())
        names = [s.name for s in bundle.sections]
        self.assertEqual(names[:4], ["rules+persona", "voice", "gap", "style"])
        self.assertEqual(names[-2:], ["late_style", "message"])

    def test_no_flags_keep_old_sections(self) -> None:
        run = build_default(_settings()).run(
            TurnInput(subject_id="s", message="노래?", persona_instructions="p"), until="assemble")
        self.assertEqual([s.name for s in run.bundle.sections], ["persona", "message"])


class RouteTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_orchestrator()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        reset_orchestrator()

    def _post(self, path: str, body: dict, **settings_kw):
        app.dependency_overrides[get_settings] = lambda: _settings(**settings_kw)
        return self.client.post(path, json=body)

    def test_responses_route_returns_memory_gap_on_both_paths(self) -> None:
        for flag in (False, True):
            with patch("infra.external_api.openai_client.OpenAI") as cls:
                cls.return_value = _mock_openai("글쎄다. [기억없음:노래]")
                r = self._post("/v1/persona/responses", dict(REQUEST, flagGaps=True), persona_orchestrator=flag)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["content"], "글쎄다.")
            self.assertEqual(r.json()["memoryGap"], {"topic": "노래"})

    def test_gap_question_route(self) -> None:
        body = {
            "candidate": {"userMessage": "할머니 좋아하던 노래 뭐였어요?", "reply": "글쎄다.", "topic": "노래",
                          "signal": "model"},
            "openQuestions": [{"id": "q1", "topic": "고향 친구", "question": "할머니께서 고향 친구 이야기를 하셨나요?"}],
            "subjectLabel": "할머니",
            "deceased": True,
        }
        answers = [
            ({"action": "new", "question": "할머니께서 즐겨 부르시던 노래가 있었나요?", "topic": "즐겨 부른 노래"},
             {"action": "new", "question": "할머니께서 즐겨 부르시던 노래가 있었나요?", "topic": "즐겨 부른 노래"}),
            ({"action": "merge", "mergeInto": "q1"}, {"action": "merge", "mergeInto": "q1"}),
            ({"action": "merge", "mergeInto": "nope"}, {"action": "skip"}),
            ({"action": "skip"}, {"action": "skip"}),
            ({"action": "new", "question": "할머니의 병명이 무엇이었나요?"}, {"action": "skip"}),
            ({"action": "new", "question": ""}, {"action": "skip"}),
        ]
        for reply, expected in answers:
            with patch("infra.external_api.openai_client.OpenAI") as cls:
                cls.return_value = _mock_openai(json.dumps(reply, ensure_ascii=False))
                r = self._post("/v1/persona/gap-question", body)
                sent = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
            self.assertEqual(r.status_code, 200, r.text)
            got = {k: v for k, v in r.json().items() if k not in ("provider", "model")}
            self.assertEqual(got, expected, reply)
            self.assertIn("TalkTo", sent[0]["content"])
            self.assertIn("돌아가신 분이다", sent[0]["content"])
            self.assertIn("id=q1", sent[1]["content"])

    def test_gap_question_bad_json_skips(self) -> None:
        req = GapQuestionRequest.model_validate({"candidate": {"userMessage": "뭐 좋아하셨어요?"}})
        with patch("infra.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai("not json")
            self.assertEqual(make_gap_question(req, settings=_settings()).action, "skip")

    def test_gap_question_tense_follows_subject(self) -> None:
        for extra, phrase in (({"deceased": False}, "살아 계신 분이다"), ({"subjectSelf": True}, "만든 분 자신이 그분이다")):
            req = GapQuestionRequest.model_validate({"candidate": {"userMessage": "뭐 좋아해요?"}, **extra})
            with patch("infra.external_api.openai_client.OpenAI") as cls:
                cls.return_value = _mock_openai('{"action": "skip"}')
                make_gap_question(req, settings=_settings())
                sent = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
            self.assertIn(phrase, sent[0]["content"])

    def _gap_question(self, body: dict, reply: dict):
        with patch("infra.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai(json.dumps(reply, ensure_ascii=False))
            r = self._post("/v1/persona/gap-question", body)
            sent = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(r.status_code, 200, r.text)
        return r.json(), sent

    def test_member_candidate_keeps_member_words_out(self) -> None:
        # 함께 쓰는 가족의 대화에서 온 후보: 그 가족의 이름·사건·사정을 옮기지 말라는 규칙이 붙고,
        # 질문 모델이 주제를 안 주면 대답 모델의 표시 주제로 채우지 않는다(만든 분이 본다, GF-4)
        candidate = {"userMessage": "할머니, 민수 결혼식에 오셨던 거 기억나요?", "reply": "글쎄다.",
                     "topic": "민수 결혼식", "signal": "model"}
        reply = {"action": "new", "question": "할머니께서 가족 잔치 때 즐겨 하시던 일이 있었나요?"}
        member, sent = self._gap_question({"candidate": candidate, "fromMember": True}, reply)
        self.assertEqual(member["question"], reply["question"])
        self.assertNotIn("topic", member)  # 표시 주제(민수 결혼식)로 채우지 않는다
        self.assertIn("함께 쓰는 다른 가족이다", sent[0]["content"])
        self.assertIn("사람 이름·호칭·장소·날짜·사건", sent[0]["content"])
        self.assertIn("그렇게 묻지 못하면 skip", sent[0]["content"])
        self.assertIn("이 말을 한 사람: 함께 쓰는 다른 가족", sent[1]["content"])
        # 질문 모델이 일반적인 주제를 주면 그것은 쓴다
        member, _ = self._gap_question({"candidate": candidate, "fromMember": True}, dict(reply, topic="가족 잔치"))
        self.assertEqual(member["topic"], "가족 잔치")

    def test_owner_candidate_prompt_is_unchanged(self) -> None:
        candidate = {"userMessage": "할머니 좋아하던 노래 뭐였어요?", "reply": "글쎄다.", "topic": "노래"}
        reply = {"action": "new", "question": "할머니께서 즐겨 부르시던 노래가 있었나요?"}
        owner, sent = self._gap_question({"candidate": candidate}, reply)
        self.assertEqual(owner["topic"], "노래")  # 만든 분 자신의 대화면 표시 주제로 채운다(예전 그대로)
        self.assertNotIn("함께 쓰는 다른 가족", sent[0]["content"])
        self.assertNotIn("이 말을 한 사람", sent[1]["content"])
        self.assertIn("- 돌아가신 분이다. 지난 일로 묻는다(…셨나요, …있었나요).\n- topic 은", sent[0]["content"])



# 가족에게 묻지 않는 화제 — 그분의 죽음·장례·임종, 위기(죽고 싶다·자해), 건강·병·약, 종교
NO_GAP_MESSAGES = (
    "할머니 돌아가실 때 마지막 모습 기억나요?",
    "할머니 장례식 때 누가 왔었는지 기억나요?",
    "요즘 너무 힘들어서 죽고 싶어요. 할머니도 그런 적 있어요?",
    "할머니 무슨 병으로 아프셨어요?",
    "할머니 무슨 약 드셨어요?",
    "할머니는 교회 다니셨어요?",
    "할머니 기도하실 때 뭐라고 하셨어요?",
)


class NoGapTopicTest(unittest.TestCase):
    def test_blocked_and_allowed_words(self) -> None:
        for text in NO_GAP_MESSAGES + ("위암으로 고생하셨잖아요", "산소는 어디예요?", "109", "절에 다니셨어요?",
                                       "할머니께서 드시던 약이 있었나요?"):
            self.assertTrue(gap_blocked(text), text)
        # 비슷한 글자의 멀쩡한 말은 막지 않는다
        for text in ("할머니 즐겨 부르던 노래 뭐였어요?", "옛날 얘기도 해 주세요", "경기도 어디 사셨어요?",
                     "할머니 아파트 몇 층이었어요?", "계절에 가장 좋아하시던 음식은요?", "믿음직한 큰아들 얘기 해 줘요",
                     "불경기 때 장사하셨어요?", "작고 예쁜 집이었어요?", "약속은 잘 지키셨어요?",
                     "명절에 드시던 약과 기억나요?", ""):
            self.assertFalse(gap_blocked(text), text)
        self.assertFalse(gap_blocked(None))

    def test_gap_instruction_says_to_skip_them(self) -> None:
        self.assertIn("죽음·장례·임종·마지막 순간", GAP_INSTRUCTION)
        self.assertIn("죽고 싶다·자해", GAP_INSTRUCTION)
        self.assertIn("건강·병·약, 종교·신앙 이야기에는 붙이지 않는다", GAP_INSTRUCTION)

    def test_plain_path_returns_no_gap_for_them(self) -> None:
        for message in NO_GAP_MESSAGES:
            result, _ = _legacy(dict(REQUEST, message=message, flagGaps=True), "그랬지. [기억없음:그때 일]")
            self.assertEqual(result.content, "그랬지.", message)  # 표시는 그래도 뗀다
            self.assertIsNone(result.memory_gap, message)
            self.assertNotIn("memoryGap", result.model_dump(by_alias=True))
        # 모델이 붙인 주제가 그런 화제여도 돌려주지 않는다
        result, _ = _legacy(dict(REQUEST, flagGaps=True), "글쎄다. [기억없음:다니던 병원]")
        self.assertIsNone(result.memory_gap)

    def test_orchestrator_returns_no_gap_for_them(self) -> None:
        class Unknown:
            impl = "unknown"

            def check(self, turn, state, selection) -> PrecheckResult:
                return PrecheckResult(known=False, coverage=0.0, topic_terms=["마지막", "모습"], directive="모른다")

        for i, message in enumerate(NO_GAP_MESSAGES):
            orch = build_default(_settings())
            orch.llm = FakeLLM("그랬지. [기억없음:그때 일]")
            run = orch.run(TurnInput(subject_id="s", message=message, persona_instructions="p",
                                     session_id=f"nogap-{i}", flag_gaps=True))
            self.assertEqual(run.result.content, "그랬지.", message)
            self.assertIsNone(run.result.memory_gap, message)
            # 사전 판정의 '기억에 없다'도 빈틈으로 치지 않는다
            orch = build_default(_settings())
            orch.llm = FakeLLM("그랬지.")
            orch.precheck = Unknown()
            run = orch.run(TurnInput(subject_id="s", message=message, persona_instructions="p", flag_gaps=True))
            self.assertIsNone(run.result.memory_gap, message)

    def _make(self, candidate: dict, reply: dict | None = None, **extra):
        req = GapQuestionRequest.model_validate({"candidate": candidate, "subjectLabel": "할머니", **extra})
        with patch("infra.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai(json.dumps(reply or {"action": "skip"}, ensure_ascii=False))
            out = make_gap_question(req, settings=_settings())
            create = cls.return_value.chat.completions.create
        return out, create

    def test_gap_question_skips_them_without_calling_the_model(self) -> None:
        asked = {"action": "new", "question": "할머니께서 즐겨 부르시던 노래가 있었나요?", "topic": "노래"}
        candidates = [{"userMessage": m, "reply": "글쎄다."} for m in NO_GAP_MESSAGES]
        candidates += [
            {"userMessage": "그때 기억나요?", "reply": "글쎄다.", "topic": "장례식"},  # 표시 주제
            {"userMessage": "그건 아니에요", "reply": "그래.", "signal": "feedback", "correction": "그때 병원에 계셨어요"},
            {"userMessage": "다 그만두고 싶어요", "reply": "많이 힘들구나. 109에 전화해 보렴."},  # 위기 대답
        ]
        for candidate in candidates:
            out, create = self._make(candidate, asked)
            self.assertEqual(out.action, "skip", candidate)
            create.assert_not_called()

    def test_gap_question_drops_model_questions_on_them(self) -> None:
        candidate = {"userMessage": "할머니 좋아하던 거 뭐였어요?", "reply": "글쎄다.", "topic": "좋아하던 것"}
        for question in ("할머니께서 다니시던 교회가 있었나요?", "할머니께서 드시던 약이 있었나요?",
                         "할머니 장례 때 부른 노래가 있었나요?"):
            out, create = self._make(candidate, {"action": "new", "question": question, "topic": "좋아하던 것"})
            create.assert_called_once()
            self.assertEqual(out.action, "skip", question)
        out, _ = self._make(candidate, {"action": "new", "question": "할머니께서 즐겨 드시던 음식이 있었나요?",
                                        "topic": "다니던 병원"})
        self.assertEqual(out.action, "skip")  # 주제가 그런 화제여도 버린다
        out, create = self._make(candidate, {"action": "new", "question": "할머니께서 즐겨 드시던 음식이 있었나요?"})
        self.assertEqual((out.action, out.question), ("new", "할머니께서 즐겨 드시던 음식이 있었나요?"))
        system = create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("죽음·장례·임종·마지막 순간", system)
        self.assertIn("건강·병·약, 종교·신앙에 관한 것이면 → skip", system)


class StyleLeadTest(unittest.TestCase):
    def test_lead_says_tone_only_and_keeps_rules(self) -> None:
        block = style_block([StyleExample(original="민수 결혼식 날 비가 왔지.", corrected="민수 장가가던 날 비 왔다.")])
        lead = block.splitlines()[1]
        self.assertTrue(lead.startswith("아래는 말투 예시일 뿐이다."))
        self.assertIn("말버릇·길이·표현만 따른다", lead)
        self.assertIn("이름·날짜·장소·일 같은 내용은 사실로 가져다 쓰지 않는다", lead)
        self.assertIn("말 높이(존댓말·반말)와 위 규칙은 그대로 지킨다", lead)
        self.assertIn("존댓말을 쓰라고 했으면 존댓말로", lead)
        self.assertIn("그대로 되풀이하지 않는다", lead)

    def test_both_paths_send_the_same_style_section(self) -> None:
        body = dict(REQUEST, styleExamples=STYLE, persona=dict(REQUEST["persona"], instructions=WITH))
        _, plain = _legacy(body, "그래.")
        expected = style_block([StyleExample(**STYLE[0])])
        self.assertEqual([m["content"] for m in plain if m["content"].startswith(STYLE_HEAD)], [expected])
        asm = build_assembler(_settings(orchestrator_assembler="structured"), "structured",
                              registry=PersonaRegistry(None))
        orch = build_default(_settings())
        turn = TurnInput(subject_id="s", message="노래?", persona_instructions=WITH,
                         style_examples=[StyleFix(**STYLE[0])], be_memories=[])
        run = orch.run(turn, until="select")
        bundle = asm.assemble(turn, run.state, run.selection, PrecheckResult())
        self.assertEqual([s.content for s in bundle.sections if s.name == "style"], [expected])
        legacy = build_default(_settings()).run(turn, until="assemble").bundle
        self.assertEqual([s.content for s in legacy.sections if s.name == "style"], [expected])


class MemberGapQuestionTest(unittest.TestCase):
    """함께 쓰는 가족의 대화에서 온 후보: 질문은 만든 분이 보므로 그 가족의 말을 옮기거나 이름·장소를 넣지 않는다."""

    CANDIDATE = {"userMessage": "할머니, 민수랑 부산 해운대 갔던 날 기억나요? 제가 그때 회사 그만뒀잖아요",
                 "reply": "글쎄다.", "topic": "해운대 여행", "signal": "model"}

    def _make(self, reply: dict, **extra):
        req = GapQuestionRequest.model_validate(
            {"candidate": self.CANDIDATE, "subjectLabel": "할머니", "fromMember": True, **extra})
        with patch("infra.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai(json.dumps(reply, ensure_ascii=False))
            out = make_gap_question(req, settings=_settings())
            messages = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
        return out, messages

    def test_prompt_carries_the_rule(self) -> None:
        _, messages = self._make({"action": "skip"})
        system = messages[0]["content"]
        for phrase in ("함께 쓰는 다른 가족이다", "그 가족이 한 말을 옮기지 않는다",
                       "따옴표로든 바꿔 말해서든 인용하지 않는다",
                       "사람 이름·호칭·장소·날짜·사건과 그 가족 자신의 사정은 질문과 topic 에 넣지 않는다",
                       "질문은 주제만 일반적으로 묻는다", "그렇게 묻지 못하면 skip"):
            self.assertIn(phrase, system)
        self.assertIn('"할머니께서 가족 잔치 때 즐겨 하시던 일이 있었나요?"', system)  # 호칭이 채워진 예
        self.assertNotIn("{label}", system)
        self.assertIn("이 말을 한 사람: 함께 쓰는 다른 가족", messages[1]["content"])

    def test_owner_prompt_has_no_member_rule(self) -> None:
        _, messages = self._make({"action": "skip"}, fromMember=False)
        self.assertNotIn("그 가족이 한 말을 옮기지 않는다", messages[0]["content"])

    def test_generic_question_passes_and_quoted_one_is_dropped(self) -> None:
        out, _ = self._make({"action": "new", "question": "할머니께서 가족과 바닷가에 가시면 즐겨 하시던 일이 있었나요?",
                             "topic": "바닷가 나들이"})
        self.assertEqual((out.action, out.topic), ("new", "바닷가 나들이"))
        for question in ('할머니께서 "해운대 갔던 날"을 기억하셨나요?', "할머니께서 ‘민수’를 뭐라고 부르셨나요?"):
            out, _ = self._make({"action": "new", "question": question, "topic": "나들이"})
            self.assertEqual(out.action, "skip", question)
        # 만든 분 자신의 대화면 따옴표가 있어도 그대로 둔다(예전과 같다)
        out, _ = self._make({"action": "new", "question": '할머니께서 "밥 먹었냐" 말고 자주 하시던 말이 있었나요?'},
                            fromMember=False)
        self.assertEqual(out.action, "new")

if __name__ == "__main__":
    unittest.main()
