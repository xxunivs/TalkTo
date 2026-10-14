"""4층 프롬프트(pipeline/persona_prompt) — 공통 규칙 모듈, 설문 → 명세, 렌더, 전사·인터뷰 생성기, 플래그."""
import json
import unittest
from itertools import product
from pathlib import Path
from types import SimpleNamespace

from pydantic import SecretStr

from infra.schema.persona import PersonaAssemblyRequest
from infra.schema.subject import IntakeContext, SubjectContext
from infra.schema.transcript import TranscriptSegment
from infra.setting import Settings
from pipeline.persona_prompt.from_intake import merge_recording, spec_from_intake, split_c10
from pipeline.persona_prompt.from_interview import apply_candidates, extract_candidates, verify
from pipeline.persona_prompt.from_transcript import card_from_utterances, shots_from_segments
from pipeline.persona_prompt.render import render_layered_prompt
from pipeline.s3_synthesis.c_build_system_prompt import assemble_persona_instructions
from pipeline.s3_synthesis.common_rules import DEATH_POLICIES, render_common_rules, render_rules

SUBJECT = {"subject": {"name": "김할매", "addressTerm": "민수야"}}
INTAKE = {
    "basicProfile": {"status": "사망", "period": "특정 시기 → 시기: 건강하던 때", "oneLine": "늘 가족 먼저",
                     "deathContext": "갑작스럽게"},
    "audience": "나와 내가 초대한 사람만",
    "familyMap": [
        {"name": "이민수", "relation": "손자", "addressTermTo": "민수야", "selfReference": "할머니가",
         "addressTermFrom": "할머니", "tone": "다정", "speechLevel": "반말", "worries": ["끼니"]},
        {"name": "이아들", "relation": "아들", "addressTermTo": "아들아", "selfReference": "엄마가",
         "addressTermFrom": "엄마", "speechLevel": "반말"},
    ],
    "speechStyle": {"length": "한두 문장으로 짧게", "sentenceEndings": ['"그래라"', '"사랑해"'],
                    "callClose": "사랑해", "avoidStyles": ["유행어"]},
    "speechExamples": [
        {"text": "\"밥 먹었냐\"", "situation": "안부", "listener": "□", "sourceQuestion": "C-09"},
        {"text": "지어낸 말", "inferred": True, "sourceQuestion": "C-09"},
        {"text": "명절과 제사는 항상 잘 챙기셨고 꼭 해야 한다고 하셨어요. 도리를 중요하게 여기셨습니다.",
         "sourceQuestion": "M4-06"},
    ],
    "personality": {"traits": ["따뜻함"]},
    "situationalReactions": [{"situation": "\"힘들어요\"라고 하면", "response": "\"잘될 거야\""}],
    "tabooTopics": [
        {"topic": "옛날 이야기를 먼저 길게 꺼내기", "grade": "금지", "sourceQuestion": "C-10"},
        {"topic": "화내거나 꾸짖는 말투", "grade": "금지", "sourceQuestion": "C-10"},
        {"topic": "그분의 사망·장례·임종", "grade": "물으면만", "sourceQuestion": "M9-01"},
        {"topic": "종교", "grade": "짧게 넘김", "sourceQuestion": "M9-01"},
        {"topic": "종교", "grade": "금지", "sourceQuestion": "M9-03"},
    ],
}


def _spec():
    return spec_from_intake(SubjectContext(**SUBJECT), IntakeContext(**INTAKE))


class RulesTest(unittest.TestCase):
    def test_layered_all_variants_render(self) -> None:
        for pub, dec, fam, pol in product([False, True], [False, True], [False, True], DEATH_POLICIES):
            text = render_rules("layered", public=pub, deceased=dec, has_family=fam, death_policy=pol,
                                vars={"death_answer": "그렇게 됐지", "family_list": "아래 가족 표"})
            self.assertNotIn("{{", text)
            self.assertIn("# 안전 가드레일 (최우선)", text)
            self.assertEqual("# 세상을 떠난 뒤의 대화" in text, dec)
            self.assertEqual("**상대가 누군지.**" in text, not pub)

    def test_death_policy_variants_are_exclusive(self) -> None:
        for pol in DEATH_POLICIES:
            text = render_rules("layered", public=False, deceased=True, has_family=True, death_policy=pol,
                                vars={"death_answer": "갑자기 그렇게 됐지"})
            self.assertEqual(text.count("사망·임종 화제는 자해와 다르다"), 1)
            self.assertEqual("갑자기 그렇게 됐지" in text, pol in ("brief", "ask_only"))

    def test_legacy_keeps_family_list_wording(self) -> None:
        self.assertIn("위 가족 목록", render_common_rules(public=False, deceased=True, has_family=True))


class IntakeSpecTest(unittest.TestCase):
    def test_creator_address_not_used_as_subject_name(self) -> None:
        spec = _spec()
        self.assertIsNone(spec.family_calls_subject)
        self.assertTrue(any(n.startswith("호칭") for n in spec.notes))
        self.assertNotIn("가족이 부르는 말: 민수야", render_layered_prompt(spec))

    def test_death_policy_single_source(self) -> None:
        spec = _spec()
        self.assertEqual((spec.death_policy, spec.death_policy_source, spec.death_answer),
                         ("ask_only", "M9-01", "갑자기 그렇게 됐지"))
        self.assertFalse(any("사망" in t.topic for t in spec.topics))
        text = render_layered_prompt(spec)
        self.assertNotIn("어떤 경우에도 입에 올리지 않는다", text)
        self.assertEqual(text.count("사망·임종 화제는 자해와 다르다"), 1)

    def test_c10_behaviors(self) -> None:
        self.assertEqual(split_c10("화내거나 꾸짖는 말투")[0], "style")
        self.assertEqual(split_c10("특정 사람 이야기를 먼저 꺼내기"), ("ask_only", "특정 사람 이야기"))
        self.assertEqual(split_c10("돈·재산·상속 이야기")[0], "taboo")
        spec = _spec()
        grades = {t.topic: t.grade for t in spec.topics}
        self.assertEqual(grades["옛날 이야기 (먼저 길게 늘어놓지 않는다)"], "물으면만")
        self.assertIn("화내거나 꾸짖는 말투", spec.style.avoid_styles)
        self.assertEqual(grades["종교"], "금지")  # 여러 문항이면 엄한 쪽

    def test_love_only_at_call_end(self) -> None:
        spec = _spec()
        self.assertEqual(spec.style.sentence_endings, ["그래라"])
        self.assertIn("통화를 끝낼 때만 (대화 중간에는 쓰지 않는다): 사랑해", render_layered_prompt(spec))

    def test_quotes_from_intake_only(self) -> None:
        spec = _spec()
        self.assertEqual([(q.text, q.listener) for q in spec.style.quotes], [("밥 먹었냐", None)])
        spec = merge_recording(spec, {"speechExamples": [{"text": "한 숟가락만 쳐"}]})
        self.assertNotIn("한 숟가락만 쳐", render_layered_prompt(spec))

    def test_layers_in_order_and_relation_table(self) -> None:
        text = render_layered_prompt(_spec())
        order = [text.index(h) for h in ("# 대화 흐름 원칙", "당신은 김할매의 페르소나다", "# 가족 (", "# 말투")]
        self.assertEqual(order, sorted(order))
        self.assertIn('- 이민수 (손자): 부르는 말 "민수야" / 이 사람 앞에서 나는 "할머니가"', text)
        self.assertIn("아래 가족 표", text)
        self.assertNotIn("위 가족 목록", text)
        self.assertIn('- "힘들어요"라고 하면 → "잘될 거야"', text)
        self.assertIn("어느 때의 모습인가: 건강하던 때", text)

    def test_merge_recording_fills_only_gaps(self) -> None:
        spec = merge_recording(_spec(), {"familyMap": [{"name": "민수", "addressTermTo": "강아지"},
                                                       {"name": "박이웃", "relation": "이웃"}],
                                         "tabooTopics": [{"topic": "죽음", "grade": "물으면만"},
                                                         {"topic": "가족 갈등", "grade": "물으면만", "note": "사실"}]})
        self.assertEqual(spec.relation_for("민수").calls_them, "민수야")  # 설문이 이긴다
        self.assertEqual(spec.relation_for("박이웃").source, "transcript")
        topics = {t.topic: t for t in spec.topics}
        self.assertNotIn("죽음", topics)
        self.assertIsNone(topics["가족 갈등"].note)  # 전사 단서(사실)는 프롬프트로 안 보낸다


def _seg(i, who, text):
    return TranscriptSegment(segment_index=i, start_ms=i, end_ms=i + 1, speaker_label=who, transcript_text=text)


class TranscriptTest(unittest.TestCase):
    def test_shots_pairs_filter_and_round_robin(self) -> None:
        segs = {"a.wav": [_seg(0, "F", "할머니 밥 먹었어?"), _seg(1, "S", "어, 먹었다. 너는?"),
                          _seg(2, "F", "나 주사 맞았어"), _seg(3, "S", "어, 병원 잘 다녀왔니"),
                          _seg(4, "F", "응 나 이제 잘게"), _seg(5, "S", "어, 그래 잘 자라")],
                "b.wav": [_seg(0, "S", "여보세요?"), _seg(1, "F", "할머니 나야 나"), _seg(2, "S", "어, 도윤이니?")]}
        labels = {"a.wav": "S", "b.wav": "S"}
        shots = shots_from_segments(segs, labels, max_shots=10)
        self.assertEqual(len(shots), 3)  # 병원·주사 쌍은 뺀다
        self.assertEqual({s.source.split("#")[0] for s in shots[:2]}, {"call:a.wav", "call:b.wav"})
        self.assertEqual(shots, shots_from_segments(segs, labels, max_shots=10))
        self.assertEqual(shots_from_segments(segs, {"a.wav": None, "b.wav": None}), [])

    def test_card_needs_enough_utterances(self) -> None:
        self.assertIsNone(card_from_utterances(["어, 그래."] * 5))
        card = card_from_utterances(["어, 그래. 밥은 먹었어?", "그러니까 조금만 넣어.", "어, 알았다."] * 12)
        self.assertIn("# 말투 카드", card)
        self.assertIn("존댓말 문장 비율 0%", card)


class InterviewTest(unittest.TestCase):
    LINES = ["0. 인터뷰", "[아버지 진술] 할머니는 정우를 '정우야'라고 부르셨다.", "[추정] 손주에게는 늘 '우리 강아지'라 했을 것"]

    def test_verify_keeps_only_quoted_evidence(self) -> None:
        items = [{"name": "이아들", "evidence": [2], "quote": "'정우야'라고 부르셨다"},
                 {"name": "x", "evidence": [99], "quote": "없음"},
                 {"name": "y", "evidence": [2], "quote": "지어낸 구절"}]
        kept, dropped = verify(items, self.LINES)
        self.assertEqual(([k["name"] for k in kept], dropped), (["이아들"], 2))

    def test_extract_and_apply_fill_empty_only(self) -> None:
        payload = {"relations": [{"name": "이아들", "calls_them": "정우야", "tone": "잔소리", "evidence": [2],
                                  "quote": "정우야"}],
                   "style": [{"field": "words", "value": "께름하다", "evidence": [2], "quote": "할머니는 정우를"}],
                   "topics": [{"topic": "사망", "grade": "자유", "evidence": [2], "quote": "부르셨다"}]}
        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))]))))
        cands = extract_candidates("\n".join(self.LINES), client=fake, model="fake")
        spec = _spec()
        log = apply_candidates(spec, cands)
        r = spec.relation_for("이아들")
        self.assertEqual((r.calls_them, r.tone), ("아들아", "잔소리"))  # 설문 값은 그대로, 빈칸만
        self.assertIn("께름하다", spec.style.words)
        self.assertFalse(any(t.topic == "사망" for t in spec.topics))
        self.assertTrue(any("interview:L2" in x for x in log))


    def test_apply_cleans_quotes_duplicates_and_descriptions(self) -> None:
        spec = _spec()
        spec.relations[0].calls_them = None
        spec.relations[1].worries = ["술·담배"]
        cands = {
            "relations": [{"name": "이민수", "calls_them": "이름으로 불렀던 것으로 정리됨", "evidence": [2]},
                          {"name": "이아들", "worries": ["담배", "운전"], "evidence": [2]}],
            "style": [{"field": "interjections", "value": "“아이고”", "evidence": [2]},
                      {"field": "words", "value": "“밥 먹었냐”, “께름하다”", "evidence": [2]},
                      {"field": "avoid_styles", "value": "유행어와 영어 표현, 반말 섞인 존댓말", "evidence": [2]}],
            "character": [{"field": "traits", "value": "따뜻하고 다정함", "evidence": [2]}],
            "topics": [{"topic": "할머니의 종교 생활", "grade": "자유", "evidence": [2]},
                       {"topic": "돈 문제", "grade": "금지", "evidence": [2]}],
        }
        log = apply_candidates(spec, cands)
        self.assertIsNone(spec.relation_for("이민수").calls_them)  # 서술은 호칭으로 쓰지 않는다
        self.assertEqual(spec.relation_for("이아들").worries, ["술·담배", "운전"])
        self.assertEqual(spec.style.interjections, ["아이고"])  # 따옴표를 벗긴다
        self.assertEqual(spec.style.words, ["께름하다"])  # 설문 표현과 같은 말은 뺀다
        self.assertEqual(spec.style.avoid_styles, ["유행어", "화내거나 꾸짖는 말투", "반말 섞인 존댓말"])
        self.assertEqual(spec.character.traits, ["따뜻함"])  # 이미 있는 것과 같은 내용
        topics = {t.topic: t.grade for t in spec.topics}
        self.assertEqual(topics["종교"], "금지")  # 같은 주제면 설문 등급 그대로
        self.assertNotIn("할머니의 종교 생활", topics)
        self.assertEqual(topics["돈 문제"], "금지")
        self.assertIn("interview", spec.materials)
        self.assertTrue(any(x.startswith("건너뜀: 이민수.calls_them") for x in log))


    def test_messy_llm_output_is_tolerated(self) -> None:
        kept, _ = verify([{"name": "x", "evidence": 2, "quote": "정우야"}], self.LINES)
        self.assertEqual(kept[0]["evidence"], [2])
        spec = _spec()
        apply_candidates(spec, {"relations": [{"name": "이아들", "worries": "운전", "speech_level": ["반말"]},
                                              {"name": "박이웃", "speech_level": ["존댓말"]}]})
        self.assertIn("운전", spec.relation_for("이아들").worries)
        self.assertEqual(spec.relation_for("박이웃").speech_level, "존댓말")


class DeathToggleTest(unittest.TestCase):
    def _req(self, death_talk=None):
        return PersonaAssemblyRequest(subject_context=SubjectContext(**SUBJECT), intake_context=IntakeContext(**INTAKE),
                                      death_talk=death_talk)

    def test_request_and_setting_override_intake(self) -> None:
        s = Settings(openai_api_key=SecretStr("sk-test")).model_copy(update={"persona_prompt_builder": "layered"})
        default = assemble_persona_instructions(self._req(), s).instructions
        self.assertIn("갑자기 그렇게 됐지", default)  # 설문 M9-01 물으면만
        off = assemble_persona_instructions(self._req(False), s).instructions
        self.assertNotIn("갑자기 그렇게 됐지", off)
        self.assertIn("직접 물어도 구체적으로 말하지 않는다", off)  # deny 변형
        s_off = s.model_copy(update={"persona_death_talk": "off"})
        self.assertNotIn("갑자기 그렇게 됐지", assemble_persona_instructions(self._req(), s_off).instructions)
        self.assertIn("갑자기 그렇게 됐지", assemble_persona_instructions(self._req(True), s_off).instructions)  # 요청이 우선


class FlagTest(unittest.TestCase):
    def test_assembly_flag(self) -> None:
        req = PersonaAssemblyRequest(subject_context=SubjectContext(**SUBJECT), intake_context=IntakeContext(**INTAKE),
                                     persona_insights=["주사 후 주의사항을 지킨다"])
        s = Settings(openai_api_key=SecretStr("sk-test"))
        legacy = assemble_persona_instructions(req, s).instructions
        layered = assemble_persona_instructions(req, s.model_copy(update={"persona_prompt_builder": "layered"}))
        self.assertNotIn("# 기억을 쓰는 법", legacy)
        self.assertIn("# 기억을 쓰는 법", layered.instructions)
        self.assertNotIn("주사 후 주의사항", layered.instructions)  # 기억 통찰은 넣지 않는다
        self.assertEqual(layered.subject_name, "김할매")

    def test_orchestrator_layered_rules(self) -> None:
        from orchestrator.assembler import StructuredAssembler
        from orchestrator.contracts import MemorySelection, PrecheckResult, SessionState, TurnInput
        from orchestrator.persona import PersonaRegistry
        reg = PersonaRegistry(Path(__file__).parents[1] / "orchestrator" / "fixtures" / "personas_restricted")
        turn = TurnInput(subject_id="s", message="안녕",
                         persona_instructions="당신은 홍길동의 페르소나를 가진 챗봇이다.\n2020년 3월 1일 가상병으로 별세.")
        args = (SessionState(session_id=None, subject_id="s"), MemorySelection(memories=[], strategy="t"), PrecheckResult())
        head = StructuredAssembler(reg, rules="layered").assemble(turn, *args).sections[0].content
        self.assertIn("몸이 안 좋아서 그렇게 갔지", head)
        self.assertNotIn("# 돌아가신 경위 질문", head)  # 별도 절 대신 모듈 변형
        self.assertEqual(head.count("사망·임종 화제는 자해와 다르다"), 1)
        legacy = StructuredAssembler(reg).assemble(turn, *args).sections[0].content
        self.assertIn("# 돌아가신 경위 질문", legacy)


if __name__ == "__main__":
    unittest.main()
