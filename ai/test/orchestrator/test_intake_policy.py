"""intake(M9-01·T1-02) → 사망·임종 정책 연결. 가짜 데이터만 쓴다."""
import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.contracts import RestrictedFacts
from orchestrator.intake_policy import death_policy
from orchestrator.persona import PersonaRegistry, merge_death_policy
from orchestrator.restricted import brief_rule


def _intake(grade=None, death_context=None, note=None):
    taboo = [{"topic": "병·수술·임종·장례 이야기를 먼저 꺼내기", "grade": "금지", "sourceQuestion": "C-10"}]
    if grade is not None:
        taboo.append({"topic": "그분의 사망·장례·임종", "grade": grade, "note": note, "sourceQuestion": "M9-01"})
    return {"intakeContext": {"basicProfile": {"status": "사망", "deathContext": death_context},
                              "tabooTopics": taboo}}


class DeathPolicyTest(unittest.TestCase):
    def test_grade_mapping(self) -> None:
        for grade, policy in [("금지", "deny"), ("짧게 넘김", "brief"), ("물으면만", "ask_only"), ("자유", "open")]:
            self.assertEqual(death_policy(_intake(grade)).policy, policy, grade)

    def test_c10_row_is_not_the_m9_grade(self) -> None:
        # C-10 의 "임종·장례 이야기를 먼저 꺼내기"(금지) 는 먼저 꺼내지 말라는 뜻이라 등급으로 쓰지 않는다
        self.assertIsNone(death_policy(_intake(None)))

    def test_declined_overrides_grade(self) -> None:
        dp = death_policy(_intake("자유", death_context="말하고 싶지 않음"))
        self.assertEqual((dp.policy, dp.source), ("deny", "T1-02"))

    def test_default_answer_from_t1_02(self) -> None:
        self.assertEqual(death_policy(_intake("물으면만", "수술·치료 중에")).default_answer, "병원에서 치료받다가 그렇게 갔지")

    def test_whole_file_or_context_only(self) -> None:
        self.assertEqual(death_policy(_intake("금지")["intakeContext"]).policy, "deny")
        self.assertIsNone(death_policy(None))


class MergeTest(unittest.TestCase):
    def test_intake_overrides_restricted_policy_keeps_facts(self) -> None:
        r = RestrictedFacts(policy="brief", brief_answer="몸이 안 좋아서 그렇게 갔지", leak_terms=["가상병"])
        m = merge_death_policy(r, _intake("물으면만", "수술·치료 중에", note="한두 마디로"))
        self.assertEqual((m.policy, m.policy_source, m.policy_note), ("ask_only", "M9-01", "한두 마디로"))
        self.assertEqual(m.brief_answer, "몸이 안 좋아서 그렇게 갔지")  # 사람이 적은 문장이 기본 문장보다 우선
        self.assertEqual(m.leak_terms, ["가상병"])

    def test_no_intake_keeps_restricted(self) -> None:
        r = RestrictedFacts(policy="brief", brief_answer="x")
        self.assertEqual(merge_death_policy(r, None).policy_source, "restricted.json")
        self.assertIsNone(merge_death_policy(None, None))

    def test_intake_without_restricted_file(self) -> None:
        m = merge_death_policy(None, _intake("짧게 넘김", "노환으로 편안하게"))
        self.assertEqual((m.policy, m.brief_answer), ("brief", "나이 들어서 편하게 갔지"))


class RuleTextTest(unittest.TestCase):
    def test_ask_only_rule(self) -> None:
        r = RestrictedFacts(policy="ask_only", brief_answer="병원에서 치료받다가 그렇게 갔지", policy_note="담담하게")
        text = brief_rule(r)
        self.assertIn("먼저 꺼내지 않는다", text)
        self.assertIn("피하지 말고", text)
        self.assertIn("설문 단서: 담담하게", text)

    def test_deny_and_open_add_nothing(self) -> None:
        for p in ("deny", "open"):
            self.assertIsNone(brief_rule(RestrictedFacts(policy=p, brief_answer="x")))


class RegistryTest(unittest.TestCase):
    def test_registry_reads_intake_json(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            pkg = Path(d) / "demo"
            pkg.mkdir()
            (pkg / "profile.json").write_text(json.dumps({"match_name": "홍길동", "deceased": True}), encoding="utf-8")
            (pkg / "restricted.json").write_text(json.dumps({"policy": "brief", "brief_answer": "x"}), encoding="utf-8")
            (pkg / "intake.json").write_text(json.dumps(_intake("금지"), ensure_ascii=False), encoding="utf-8")
            prof = PersonaRegistry(Path(d)).by_name["홍길동"]
        self.assertEqual((prof.restricted.policy, prof.restricted.policy_source), ("deny", "M9-01"))
