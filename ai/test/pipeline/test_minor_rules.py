"""조립 때 미성년 규칙(prompts/rules/minor_safety.md) — minor 가 아니면 출력이 바이트까지 예전과 같고,
minor 면 안전 절 self_harm 바로 뒤에 규칙 6줄(legacy·layered 두 세트). 고인(떠나신 자녀)이면 넣지 않는다. 가짜 데이터만.
"""
import json
import unittest
from itertools import product
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.schema.subject import IntakeContext, SubjectContext
from app.setting import Settings, get_settings
from main import app
from pipeline.persona_prompt.from_intake import spec_from_intake
from pipeline.persona_prompt.layers import static_flags
from pipeline.persona_prompt.render import render_layered_prompt
from pipeline.s3_synthesis.c_build_system_prompt import fixed_rules
from pipeline.s3_synthesis.common_rules import RULES_DIR, render_common_rules, render_rules
from test.pipeline.test_persona_prompt import INTAKE, SUBJECT

FIXTURES = Path(__file__).parent / "fixtures"
MINOR_LINES = [ln for ln in (RULES_DIR / "minor_safety.md").read_text(encoding="utf-8").splitlines()
               if ln.startswith("- ")]
SELF_HARM = "- **자해·자살 암시에는 즉시 대응한다.**"
SAFETY_TITLE = "# 안전 가드레일 (최우선)"


def _golden(pub: bool, dec: bool, fam: bool) -> str:
    name = f"{'public' if pub else 'private'}_{'deceased' if dec else 'alive'}_{'family' if fam else 'nofamily'}.txt"
    return (FIXTURES / "common_rules_golden" / name).read_text(encoding="utf-8")


def spec(**intake_update):
    ic = json.loads(json.dumps(INTAKE))
    ic.update(intake_update)
    return spec_from_intake(SubjectContext(**SUBJECT), IntakeContext(**ic))


def assert_after_self_harm(case: unittest.TestCase, text: str) -> None:
    """규칙 6줄이 한 번, 안전 절 안 self_harm 줄 바로 뒤에."""
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith(SELF_HARM))
    case.assertEqual(lines[i + 1:i + 7], MINOR_LINES)
    case.assertEqual(text.count(MINOR_LINES[0]), 1)
    case.assertLess(text.rfind(SAFETY_TITLE), text.find(MINOR_LINES[0]))


class NotMinorUnchangedTest(unittest.TestCase):
    def test_legacy_set_matches_golden(self) -> None:
        for pub, dec, fam in product([False, True], repeat=3):
            with self.subTest(public=pub, deceased=dec, family=fam):
                golden = _golden(pub, dec, fam)
                self.assertEqual(render_common_rules(public=pub, deceased=dec, has_family=fam, minor=False), golden)
                self.assertEqual(fixed_rules(public=pub, deceased=dec, has_family=fam, minor=False), golden)

    def test_layered_set_and_prompt_unchanged(self) -> None:
        for pub, dec, fam in product([False, True], repeat=3):
            with self.subTest(public=pub, deceased=dec, family=fam):
                self.assertEqual(render_rules("layered", public=pub, deceased=dec, has_family=fam, minor=False),
                                 render_rules("layered", public=pub, deceased=dec, has_family=fam))
                self.assertNotIn(MINOR_LINES[0], render_rules("layered", public=pub, deceased=dec, has_family=fam))
        golden = (FIXTURES / "layered_golden" / "alive.txt").read_text(encoding="utf-8")
        s = spec(basicProfile={"status": "생존", "minor": False})
        self.assertFalse(s.minor)
        self.assertEqual(render_layered_prompt(s), golden)
        self.assertNotIn("minor", static_flags(s))


class MinorRulesTest(unittest.TestCase):
    def test_both_sets_put_rules_after_self_harm(self) -> None:
        for set_name, pub, fam in product(("legacy", "layered"), [False, True], [False, True]):
            with self.subTest(set=set_name, public=pub, family=fam):
                text = render_rules(set_name, public=pub, deceased=False, has_family=fam, minor=True)
                assert_after_self_harm(self, text)
                # 규칙 6줄만 더해진다
                plain = render_rules(set_name, public=pub, deceased=False, has_family=fam)
                self.assertEqual(text.replace("\n".join(MINOR_LINES) + "\n", ""), plain)

    def test_deceased_child_gets_no_minor_rules(self) -> None:
        for set_name, pub, fam in product(("legacy", "layered"), [False, True], [False, True]):
            with self.subTest(set=set_name, public=pub, family=fam):
                self.assertEqual(render_rules(set_name, public=pub, deceased=True, has_family=fam, minor=True),
                                 render_rules(set_name, public=pub, deceased=True, has_family=fam))

    def test_spec_from_intake_reads_basic_profile_minor(self) -> None:
        s = spec(basicProfile={"status": "생존", "minor": True})
        self.assertTrue(s.minor)
        self.assertIn("minor", static_flags(s))
        assert_after_self_harm(self, render_layered_prompt(s))
        # 떠나신 자녀는 고인 규칙만
        dead = spec(basicProfile={"status": "사망", "minor": True})
        self.assertFalse(dead.minor)
        self.assertNotIn(MINOR_LINES[0], render_layered_prompt(dead))
        # bool true 만(BE 는 bool 만 보낸다)
        self.assertFalse(spec(basicProfile={"status": "생존", "minor": "true"}).minor)
        self.assertFalse(spec(basicProfile={"status": "생존"}).minor)


class AssemblyEndpointTest(unittest.TestCase):
    """POST /v1/persona/assembly — intakeContext.basicProfile.minor(dict 라 스키마 변경 없음)."""

    def _assemble(self, builder: str, basic: dict) -> str:
        app.dependency_overrides[get_settings] = lambda: Settings(
            openai_api_key=SecretStr("test-key"), persona_prompt_builder=builder)
        self.addCleanup(app.dependency_overrides.pop, get_settings, None)
        r = TestClient(app).post("/v1/persona/assembly", json={
            "subjectContext": {"subject": {"name": "김민지", "addressTerm": "민지야"}},
            "intakeContext": {"basicProfile": basic},
        })
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["instructions"]

    def test_minor_rules_in_both_builders(self) -> None:
        for builder in ("layered", "legacy"):
            with self.subTest(builder):
                assert_after_self_harm(self, self._assemble(builder, {"status": "생존", "minor": True}))
                self.assertNotIn(MINOR_LINES[0], self._assemble(builder, {"status": "생존"}))
                self.assertNotIn(MINOR_LINES[0], self._assemble(builder, {"status": "사망", "minor": True}))


if __name__ == "__main__":
    unittest.main()
