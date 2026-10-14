"""공통 규칙 md 분리 — 골든 = fixed_rules 코드 문장 8개 변형."""
import unittest
from itertools import product
from pathlib import Path

from pipeline.s3_synthesis.c_build_system_prompt import fixed_rules
from pipeline.s3_synthesis.common_rules import common_rule_lines, render_common_rules

GOLDEN = Path(__file__).parent / "fixtures" / "common_rules_golden"


def _name(pub: bool, dec: bool, fam: bool) -> str:
    return (f"{'public' if pub else 'private'}_{'deceased' if dec else 'alive'}_"
            f"{'family' if fam else 'nofamily'}.txt")


class CommonRulesTest(unittest.TestCase):
    def test_all_variants_match_golden(self) -> None:
        for pub, dec, fam in product([False, True], repeat=3):
            with self.subTest(public=pub, deceased=dec, family=fam):
                golden = (GOLDEN / _name(pub, dec, fam)).read_text(encoding="utf-8")
                self.assertEqual(render_common_rules(public=pub, deceased=dec, has_family=fam), golden)
                self.assertEqual(fixed_rules(public=pub, deceased=dec, has_family=fam), golden)

    def test_safety_lines_present_everywhere(self) -> None:
        for pub, dec, fam in product([False, True], repeat=3):
            text = render_common_rules(public=pub, deceased=dec, has_family=fam)
            self.assertIn("# 안전 가드레일 (최우선)", text)
            self.assertIn("자해·자살 암시에는 즉시 대응한다", text)
            self.assertIn("의료·법률·금전 조언 금지", text)
            # 가족용 변형에도 109(자살예방상담전화) 안내
            self.assertIn("109(자살예방상담전화)", text)

    def test_rule_lines_for_dedupe(self) -> None:
        lines = common_rule_lines(public=False, deceased=True, has_family=True)
        self.assertTrue(all(ln.startswith("- ") for ln in lines))
        self.assertEqual(len(lines), 12)  # 대화 설정 비공개 규칙 포함
