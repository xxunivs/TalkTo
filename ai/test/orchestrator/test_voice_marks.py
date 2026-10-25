"""중간계층 음성 표시(FISH_EMOTION) — 실제 API 호출 없음.

생성 바로 뒤에 감정 이름·연기 표시를 떼어 가드·언어 가드·턴 뒤 처리는 깨끗한 글만 보고, 결과에 emotion·speechText 를
싣는다. 기존 경로(generate_persona_response)와 같은 메시지·같은 결과를 낸다.
"""
import importlib.util
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.schema.persona import PersonaResponseRequest
from app.setting import Settings, get_settings
from main import app
from orchestrator.contracts import (
    GuardResult,
    MemorySelection,
    PrecheckResult,
    SessionState,
    TurnInput,
)
from orchestrator.guard import GuardV1, PassGuard
from orchestrator.lang_guard import LanguageGuard
from orchestrator.llm import FakeLLM
from orchestrator.persona import PersonaRegistry
from orchestrator.service import (
    build_assembler,
    build_default,
    orchestrate_persona_response,
    reset_orchestrator,
)
from orchestrator.voice import peel_header
from pipeline.s3_synthesis.c_build_system_prompt import STYLE_LATE_NOTE, generate_persona_response
from pipeline.s4_voice.b_emotion import FISH_CUE_INSTRUCTION, FISH_INLINE_INSTRUCTION
from test.pipeline.test_late_style_note import WITH

HAS_KIWI = importlib.util.find_spec("kiwipiepy") is not None
FISH = "fish_0123abcd"
REQUEST = {
    "message": "할머니 고향이 어디셨어요?",
    "history": [{"role": "user", "content": "안녕하세요"}, {"role": "assistant", "content": "어, 왔니"}],
    "memories": [{"id": "m1", "title": "고향", "content": "홍길동은 남원에서 태어났다."}],
    "memoryTotal": 300,  # 기억 많은 페르소나(기억 적음 절 없음 — test_low_memory 가 따로 본다)
    "persona": {"subjectId": "subj-1", "instructions": "당신은 홍길동이다.", "voiceId": FISH},
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


class SpyGuard:
    """받은 글을 모아 두는 가드(안쪽은 PassGuard)."""
    impl = "spy"

    def __init__(self, inner=None) -> None:
        self.inner = inner or PassGuard()
        self.seen: list[str] = []

    def apply(self, generation, turn, state, selection) -> GuardResult:
        self.seen.append(generation.text)
        return self.inner.apply(generation, turn, state, selection)


def _turn(message: str = "할머니 저 왔어요", **kw) -> TurnInput:
    return TurnInput(subject_id="s", message=message, persona_instructions="당신은 홍길동이다.", voice_id=FISH, **kw)


def _orch(fish_emotion: str, reply: str, guard=None):
    orch = build_default(_settings(fish_emotion=fish_emotion))
    orch.llm = FakeLLM(reply)
    if guard is not None:
        orch.guard = guard
    return orch


class SameAsPlainPathTest(unittest.TestCase):
    """legacy 조립 구성은 두 방식 모두 기존 경로와 같은 메시지·같은 결과를 낸다(가족 공유 화자 힌트 포함)."""

    def _both(self, settings: Settings, reply: str, **extra):
        req = PersonaResponseRequest.model_validate({**REQUEST, **extra})
        with patch("app.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai(reply)
            plain = generate_persona_response(req, settings=settings)
            plain_messages = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
        with patch("app.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai(reply)
            orch = orchestrate_persona_response(req, settings=settings, orchestrator=build_default(settings))
            orch_messages = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
            self.assertEqual(cls.return_value.chat.completions.create.call_count, 1)  # LLM 을 더 부르지 않는다
        self.assertEqual(orch_messages, plain_messages)
        self.assertEqual(orch.model_dump(), plain.model_dump())
        return orch, orch_messages

    def test_cue(self) -> None:
        res, messages = self._both(_settings(fish_emotion="cue"), "[감정:sad] 남원이지.")
        self.assertEqual((res.content, res.emotion, res.speech_text), ("남원이지.", "calm", None))  # 고인 여부 모름 → 고인
        self.assertEqual(messages[1], {"role": "system", "content": FISH_CUE_INSTRUCTION})

    def test_cue_living(self) -> None:
        persona = dict(REQUEST["persona"], deceased=False)
        res, _ = self._both(_settings(fish_emotion="cue"), "[감정:sad] 남원이지.", persona=persona)
        self.assertEqual(res.emotion, "sad")

    def test_inline_with_member_speaker_hint(self) -> None:
        res, messages = self._both(_settings(fish_emotion="inline"), "[웃으며] 남원이지. [미소] 거기서 컸어.",
                                   sessionId="sess-1", speakerHint="박민호")
        self.assertEqual(res.content, "남원이지. 거기서 컸어.")
        self.assertEqual(res.speech_text, "[웃으며] 남원이지. 거기서 컸어.")
        self.assertIsNone(res.emotion)
        self.assertEqual(messages[1], {"role": "system", "content": FISH_INLINE_INSTRUCTION})

    def test_off_is_unchanged(self) -> None:
        res, messages = self._both(_settings(), "[웃으며] 남원이지.")
        self.assertEqual((res.content, res.emotion, res.speech_text), ("[웃으며] 남원이지.", None, None))
        self.assertNotIn("speechText", res.model_dump(by_alias=True))
        self.assertEqual([m["role"] for m in messages].count("system"), 2)  # 지시문 + 참고 기억

    def test_not_fish_voice_is_off(self) -> None:
        persona = dict(REQUEST["persona"], voiceId="TalkToabc")
        res, messages = self._both(_settings(fish_emotion="inline"), "[웃으며] 남원이지.", persona=persona)
        self.assertEqual((res.content, res.speech_text), ("[웃으며] 남원이지.", None))
        self.assertFalse(any(m["content"] == FISH_INLINE_INSTRUCTION for m in messages))


class GuardSeesCleanTextTest(unittest.TestCase):
    def test_guard_lang_guard_and_post_turn_see_clean_text(self) -> None:
        spy = SpyGuard()
        orch = _orch("inline", "[웃으며] 그래, 왔구나. [작게] 밥은 먹었니?",
                     guard=LanguageGuard(spy, PersonaRegistry(None), mode="flag"))
        run = orch.run(_turn(session_id="A"))
        self.assertEqual(spy.seen, ["그래, 왔구나. 밥은 먹었니?"])
        self.assertEqual(run.generation.text, "[웃으며] 그래, 왔구나. [작게] 밥은 먹었니?")  # 모델 글은 그대로 남긴다
        self.assertEqual(run.result.content, "그래, 왔구나. 밥은 먹었니?")
        self.assertEqual(run.result.speech_text, "[웃으며] 그래, 왔구나. [작게] 밥은 먹었니?")
        self.assertEqual(run.next_state.recent_turns[-1].content, "그래, 왔구나. 밥은 먹었니?")
        self.assertEqual(run.trace[-3].stage, "voice")  # 생성 → 음성 표시 → 가드 → 턴 뒤
        self.assertEqual(run.trace[-3].summary["cues"], ["[웃으며]", "[작게]"])

    def test_cue_name_never_reaches_the_guard(self) -> None:
        spy = SpyGuard()
        run = _orch("cue", "[감정:worried] 늦게 다니지 마라.", guard=spy).run(_turn(deceased=False))
        self.assertEqual(spy.seen, ["늦게 다니지 마라."])
        self.assertEqual((run.result.content, run.result.emotion), ("늦게 다니지 마라.", "worried"))

    def test_off_adds_no_stage(self) -> None:
        spy = SpyGuard()
        run = _orch("off", "[웃으며] 왔구나.", guard=spy).run(_turn())
        self.assertEqual(spy.seen, ["[웃으며] 왔구나."])
        self.assertNotIn("voice", [t.stage for t in run.trace])
        self.assertIsNone(run.result.speech_text)

    def test_header_stays_first_for_the_guard(self) -> None:
        # 근거 헤더(guard v1·v11)는 가드가 읽어야 한다. 표시가 헤더 앞에 와도 헤더를 맨 앞에 둔다
        self.assertEqual(peel_header("[감정:happy] [근거: m0]\n남원이지."), ("[근거: m0]", "[감정:happy] 남원이지."))
        self.assertEqual(peel_header("[근거: m0] [웃으며] 남원이지."), ("[근거: m0]", "[웃으며] 남원이지."))
        self.assertEqual(peel_header("[웃으며] 남원이지."), ("", "[웃으며] 남원이지."))
        for reply, emotion in (("[감정:happy] [근거: m0]\n남원이지.", "happy"), ("[근거: m0]\n[감정:happy] 남원이지.", "happy")):
            spy = SpyGuard()
            run = _orch("cue", reply, guard=spy).run(_turn(deceased=False))
            self.assertEqual(spy.seen, ["[근거: m0]\n남원이지."], reply)
            self.assertEqual(run.result.emotion, emotion)


@unittest.skipUnless(HAS_KIWI, "kiwipiepy 필요")
class WithRealGuardTest(unittest.TestCase):
    """가드 v1 이 문장을 지우면 그 문장의 표시도 빠지고, 답을 갈아 끼우면 감정·표시를 버린다."""

    def _run(self, mode: str, reply: str, message: str, memories=()):
        orch = _orch(mode, reply, guard=GuardV1(PersonaRegistry(None)))
        sel_turn = _turn(message, deceased=True, be_memories=list(memories))
        return orch.run(sel_turn)

    def test_trimmed_sentence_takes_its_cue_with_it(self) -> None:
        run = self._run("inline", "[웃으며] 그래, 왔구나. [다정하게] 사랑한다.", "할머니 저 왔어요")
        self.assertEqual(run.guard.action, "trim")
        self.assertEqual(run.result.content, "그래, 왔구나.")
        self.assertEqual(run.result.speech_text, "[웃으며] 그래, 왔구나.")

    def test_header_cited_and_cues_kept(self) -> None:
        from orchestrator.contracts import MemoryItem

        mem = MemoryItem(id="m1", title="고향", text="홍길동은 남원에서 태어났다.")
        run = self._run("inline", "[웃으며] [근거: m0]\n남원이지. [작게] 거기서 컸지.", "할머니 고향이 어디셨어요?", [mem])
        self.assertEqual(run.guard.cited_memory_ids, ["m1"])
        self.assertEqual(run.result.content, "남원이지. 거기서 컸지.")
        self.assertEqual(run.result.speech_text, "[웃으며] 남원이지. [작게] 거기서 컸지.")

    def test_ask_back_drops_emotion_and_cues(self) -> None:
        for mode, reply in (("cue", "[감정:happy] [근거: 없음]\n제주도 갔었지."),
                            ("inline", "[근거: 없음]\n[웃으며] 제주도 갔었지.")):
            run = self._run(mode, reply, "할머니 신혼여행 어디로 가셨어요?")
            self.assertEqual(run.guard.action, "ask_back", mode)
            self.assertIsNone(run.result.emotion, mode)
            self.assertIsNone(run.result.speech_text, mode)
            self.assertNotIn("[", run.result.content, mode)


class AssemblerPlacementTest(unittest.TestCase):
    """음성 지침은 첫 system 바로 뒤 별도 system. 말투 덧붙임·존댓말 판정은 그대로다."""

    ARGS = (SessionState(session_id=None, subject_id="s"), MemorySelection(memories=[], strategy="t"), PrecheckResult())

    def test_both_assemblers(self) -> None:
        turn = TurnInput(subject_id="s", message="밥 먹었어?", persona_instructions=WITH, voice_id=FISH)
        for kind in ("legacy", "structured"):
            for mode, text in (("cue", FISH_CUE_INSTRUCTION), ("inline", FISH_INLINE_INSTRUCTION)):
                secs = build_assembler(_settings(fish_emotion=mode), kind).assemble(turn, *self.ARGS).sections
                self.assertEqual((secs[1].name, secs[1].role, secs[1].content), ("voice", "system", text), kind)
                self.assertEqual(secs[-2].content, STYLE_LATE_NOTE, kind)  # 말투 덧붙임은 그대로 마지막 말 앞
            off = build_assembler(_settings(), kind).assemble(turn, *self.ARGS).sections
            self.assertNotIn("voice", [s.name for s in off], kind)

    def test_structured_header_mode(self) -> None:
        settings = _settings(fish_emotion="inline", orchestrator_assembler="structured", orchestrator_guard="v1")
        turn = TurnInput(subject_id="s", message="밥 먹었어?", persona_instructions=WITH, voice_id=FISH)
        secs = build_assembler(settings).assemble(turn, *self.ARGS).sections
        self.assertIn("[근거:", secs[0].content)  # 근거 헤더 규칙은 첫 system 에 그대로
        self.assertEqual(secs[1].name, "voice")

    def test_polite_listener_keeps_polite_rules(self) -> None:
        # 가족 공유: 존댓말로 대하는 사위가 대화하면 말투 덧붙임(반말 예시)은 빠지고 음성 지침만 들어간다
        from test.orchestrator.test_polite_ask_back import _layered

        polite = _layered("존댓말")
        turn = TurnInput(subject_id="s", message="장모님 저 왔어요", persona_instructions=polite, voice_id=FISH)
        for kind in ("legacy", "structured"):
            secs = build_assembler(_settings(fish_emotion="inline"), kind).assemble(turn, *self.ARGS).sections
            contents = [s.content for s in secs]
            self.assertIn(FISH_INLINE_INSTRUCTION, contents, kind)
            self.assertNotIn(STYLE_LATE_NOTE, contents, kind)


class RouteTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_orchestrator()

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        reset_orchestrator()

    def _post(self, body: dict, reply: str, **kw):
        app.dependency_overrides[get_settings] = lambda: _settings(persona_orchestrator=True, **kw)
        with patch("app.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai(reply)
            return TestClient(app).post("/v1/persona/responses", json=body)

    def test_orchestrator_route_returns_speech_text(self) -> None:
        r = self._post(dict(REQUEST, speakerHint="박민호"), "[웃으며] 남원이지.", fish_emotion="inline")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["content"], "남원이지.")
        self.assertEqual(r.json()["speechText"], "[웃으며] 남원이지.")

    def test_fixed_prompt_route_returns_speech_text(self) -> None:
        body = dict(REQUEST, persona=dict(REQUEST["persona"], fixedPrompt=True))
        r = self._post(body, "[감정:warm] 남원이지.", fish_emotion="cue")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["content"], r.json()["emotion"]), ("남원이지.", "warm"))
        self.assertNotIn("speechText", r.json())

    def test_off_route_has_no_speech_text_key(self) -> None:
        r = self._post(REQUEST, "[웃으며] 남원이지.")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("speechText", r.json())
        self.assertIsNone(r.json()["emotion"])
