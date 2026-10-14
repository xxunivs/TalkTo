"""제한 정보(사인 등) 별도 파일 — 프롬프트·기억에서 빼고 restricted.json 에서만 관리."""
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import SecretStr

from infra.setting import Settings
from orchestrator.contracts import MemoryItem, RestrictedFacts, TurnInput
from orchestrator.llm import FakeLLM
from orchestrator.restricted import brief_rule, redact_instructions
from orchestrator.service import build_default

PDIR = Path(__file__).parent / "fixtures" / "personas_restricted"
PERSONA = ("당신은 홍길동의 페르소나를 가진 챗봇이다.\n\n# 기본 정보\n홍길동. 남원 출신. 2020년 3월 1일 가상병으로 별세.  불교.\n\n"
           "## 가족\n- **철수** (아들, 임종을 지킴): 챙기는 톤.\n  - 들여쓰기  유지\n")
MEMS = [MemoryItem(id="m1", text="된장찌개를 잘 끓였다."),
        MemoryItem(id="m-death", text="병원에서 돌아가셨다."),
        MemoryItem(id="m3", text="가족이 가상병 얘기를 했다.")]


def _orch(assembler="structured", persona_dir=PDIR):
    s = Settings(openai_api_key=SecretStr("k"), orchestrator_assembler=assembler, orchestrator_persona_dir=persona_dir)
    o = build_default(s)
    o.llm = FakeLLM()
    return o


class RestrictedTest(unittest.TestCase):
    def test_redact_reports_missing_and_keeps_indent(self) -> None:
        r = RestrictedFacts.model_validate(json.loads((PDIR / "demo" / "restricted.json").read_text(encoding="utf-8")))
        text, missing = redact_instructions(PERSONA, r)
        self.assertNotIn("가상병", text)
        self.assertNotIn("임종", text)
        self.assertIn("2020년 별세. 불교.", text)
        self.assertIn("- **철수** (아들): 챙기는 톤.", text)
        self.assertIn("\n  - 들여쓰기 유지", text)
        self.assertEqual(missing, ["없는 문장."])

    def test_no_restricted_is_identity(self) -> None:
        self.assertEqual(redact_instructions(PERSONA, None), (PERSONA, []))
        self.assertIsNone(brief_rule(None))
        self.assertIsNone(brief_rule(RestrictedFacts(policy="deny", brief_answer="x")))

    def test_structured_path_drops_memories_and_adds_brief_rule(self) -> None:
        run = _orch().run(TurnInput(subject_id="s", message="어떻게 돌아가셨어?", persona_instructions=PERSONA,
                                    be_memories=MEMS), until="assemble")
        self.assertEqual([m.id for m in run.selection.memories], ["m1"])
        self.assertTrue(run.selection.strategy.endswith("restricted-2"))
        full = "\n".join(s.content for s in run.bundle.sections)
        self.assertNotIn("가상병", full)
        self.assertIn("몸이 안 좋아서 그렇게 갔지", full)
        self.assertIn("돌아가신 경위 질문", full)

    def test_legacy_path_untouched(self) -> None:
        run = _orch(assembler="legacy").run(TurnInput(subject_id="s", message="m", persona_instructions=PERSONA,
                                                      be_memories=MEMS), until="assemble")
        self.assertEqual(len(run.selection.memories), 3)
        self.assertIn("가상병", run.bundle.sections[0].content)

    def test_other_persona_unaffected(self) -> None:
        other = "당신은 김다른의 페르소나를 가진 챗봇이다.\n가상병 언급."
        run = _orch().run(TurnInput(subject_id="s", message="m", persona_instructions=other, be_memories=MEMS),
                          until="assemble")
        self.assertEqual(len(run.selection.memories), 3)

    def test_deny_policy_has_no_brief_rule(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            pkg = Path(d) / "demo"
            pkg.mkdir()
            (pkg / "profile.json").write_text((PDIR / "demo" / "profile.json").read_text(encoding="utf-8"), encoding="utf-8")
            data = json.loads((PDIR / "demo" / "restricted.json").read_text(encoding="utf-8"))
            data["policy"] = "deny"
            (pkg / "restricted.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            run = _orch(persona_dir=Path(d)).run(
                TurnInput(subject_id="s", message="m", persona_instructions=PERSONA, be_memories=MEMS), until="assemble")
        full = "\n".join(s.content for s in run.bundle.sections)
        self.assertNotIn("돌아가신 경위 질문", full)
        self.assertNotIn("가상병", full)
