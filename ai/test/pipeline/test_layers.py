"""층 구성(prompts/layers.json): 상황별로 켜지는 모듈, 기본 구성 골든, 주고받기 위치, 런타임 절."""
import json
import tempfile
import unittest
from pathlib import Path

from app.schema.subject import IntakeContext, SubjectContext
from pipeline.persona_prompt.from_intake import spec_from_intake
from pipeline.persona_prompt.layers import (
    RuntimeContext,
    load_layers,
    runtime_section,
    static_modules,
)
from pipeline.persona_prompt.render import render_layered_prompt, shot_messages
from pipeline.persona_prompt.spec import Relation, Shot
from test.pipeline.test_persona_prompt import INTAKE, SUBJECT

GOLDEN = Path(__file__).parent / "fixtures" / "layered_golden"


def spec(**intake_update):
    ic = json.loads(json.dumps(INTAKE))
    ic.update(intake_update)
    return spec_from_intake(SubjectContext(**SUBJECT), IntakeContext(**ic))


class StaticLayersTest(unittest.TestCase):
    def test_default_configuration_matches_golden(self) -> None:
        for name, s in (("default", spec()), ("public", spec(audience="공개")),
                        ("alive", spec(basicProfile={"status": "생존"}))):
            with self.subTest(name):
                self.assertEqual(render_layered_prompt(s), (GOLDEN / f"{name}.txt").read_text(encoding="utf-8"))

    def test_modules_follow_persona_kind(self) -> None:
        private, public = static_modules(spec()), static_modules(spec(audience="공개"))
        self.assertIn("family_table", private)
        self.assertNotIn("visitors", private)
        self.assertIn("visitors", public)
        self.assertNotIn("family_table", public)

    def test_public_deceased_speaks_to_visitors(self) -> None:
        text = render_layered_prompt(spec(audience="공개"))
        self.assertIn("방문자에게 전하는 서비스", text)
        self.assertNotIn("가족에게 대신 전달", text)

    def test_alive_drops_afterlife_rules(self) -> None:
        self.assertIn("# 세상을 떠난 뒤의 대화", render_layered_prompt(spec()))
        self.assertNotIn("# 세상을 떠난 뒤의 대화", render_layered_prompt(spec(basicProfile={"status": "생존"})))

    def test_recording_only_keeps_identity_and_style(self) -> None:
        s = spec_from_intake(SubjectContext(subject={"name": "김할매"}), None)
        s.style.card = "# 말투 카드\n- 짧게 말한다"
        s.style.shots = [Shot(user="밥 먹었어?", persona="어, 먹었다")]
        text = render_layered_prompt(s)
        self.assertEqual(s.materials, [])
        self.assertIn("당신은 김할매의 페르소나다", text)
        self.assertIn("# 말투 카드", text)
        self.assertIn("가족: 밥 먹었어?", text)
        self.assertNotIn("# 성격·가치관", text)

    def test_survey_only_has_no_measured_style(self) -> None:
        text = render_layered_prompt(spec())
        self.assertNotIn("# 말투 카드", text)
        self.assertNotIn("# 실제 통화에서", text)
        self.assertIn("# 실제로 하던 말", text)

    def test_shots_as_messages(self) -> None:
        s = spec()
        s.style.shots = [Shot(user="밥 먹었어?", persona="어, 먹었다")]
        self.assertIn("# 실제 통화에서", render_layered_prompt(s))
        self.assertNotIn("# 실제 통화에서", render_layered_prompt(s, shots_mode="messages"))
        self.assertEqual(shot_messages(s), [{"role": "user", "content": "밥 먹었어?"},
                                            {"role": "assistant", "content": "어, 먹었다"}])

    def test_drop_module_for_ablation(self) -> None:
        s = spec()
        s.style.card = "# 말투 카드\n- 짧게"
        self.assertNotIn("# 말투 카드", render_layered_prompt(s, drop=["style_card"]))

    def test_unnamed_family_member_shows_relation_once(self) -> None:
        s = spec()
        s.relations.append(Relation(name="며느리", relation="며느리", they_call="어머니"))
        self.assertIn('- 며느리: 이 사람은 나를 "어머니"라고 부름', render_layered_prompt(s))

    def test_manifest_rejects_unknown_condition(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "layers.json"
            bad.write_text(json.dumps({"static": [{"layer": "L1", "module": "x", "when": ["nope"]}], "runtime": []}),
                           encoding="utf-8")
            with self.assertRaises(ValueError):
                load_layers(bad)


class RuntimeLayersTest(unittest.TestCase):
    def test_known_speaker_restates_row(self) -> None:
        text = runtime_section(spec(), RuntimeContext(speaker="민수"))
        self.assertIn('지금 말하는 사람: 이민수 (손자) — 부르는 말 "민수야"', text)

    def test_stranger_is_not_treated_as_family(self) -> None:
        self.assertIn("가족 표에 없는 사람", runtime_section(spec(), RuntimeContext(speaker="박이웃")))

    def test_unknown_speaker_asks(self) -> None:
        self.assertIn("아직 모른다", runtime_section(spec(), RuntimeContext()))
        self.assertEqual(runtime_section(spec(audience="공개"), RuntimeContext()), "")

    def test_long_conversation_only_mentions_what_was_said(self) -> None:
        hist = [("user", "할머니"), ("assistant", "어, 밥은 먹었니?")] * 3
        text = runtime_section(spec(), RuntimeContext(speaker="이민수", history=hist))
        self.assertIn("밥·건강은 이번 대화에서 이미 챙겼다", text)
        self.assertNotIn("사랑해", text)
        short = runtime_section(spec(), RuntimeContext(speaker="이민수", history=hist[:2]))
        self.assertNotIn("이미 챙겼다", short)


if __name__ == "__main__":
    unittest.main()
