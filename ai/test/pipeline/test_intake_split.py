"""작성본(md) 읽기·나누기(pipeline/s0_intake) — 2026-10-21 개정 문항 머리(〔필수〕 표시),
'기억이 안 나요'·'따로 없었어요' 표시, 새 문항 M6-08(그분 하면 떠오르는 것 → 기억)."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from pipeline.s0_intake.a_read_intake import QID, parse
from pipeline.s0_intake.b_split_intake import to_intake, to_memories

DOC = Path(__file__).resolve().parents[2] / "docs" / "Persona_Intake.md"
LEAKS = ("기억이 안 나", "따로 없었", "__unknown__", "__none__")

SHEET = """# PART 2. Core

**C-01 · 그분은 말할 때 보통 어느 정도 길이로 말하셨어요?** 〔필수〕  `하나 선택`

- ■ ① 한두 문장으로 짧게
- □ ② 서너 문장 정도

**C-05 · 그분을 한 문장으로 표현한다면?** 〔선택 — 2026-10-21 필수에서 내림: 요약하기 어려운 질문〕  `단답 40자`

____________________

**C-07 · 그분은 나를 어떻게 챙기셨어요?** 〔필수〕  `최대 3개`

- ■ ② 음식·밥을 챙겨서
- □ ③ 용돈·선물·물건을 챙겨서

**C-09 · 그분이 나에게 자주 하시던 말을 한 줄에 하나씩 적어주세요. 걱정·당부·잔소리도 좋아요.**  `표 1~10행` 〔필수 · 1줄 이상〕

| 표현<br>실제 표현 그대로 | 언제 쓰는 말인가요 | 누구에게 | 추정 |
|---|---|---|---|
| 밥은 먹었냐 | 걱정·당부 | 나 | □ |

# PART 3. 표준 모듈

**M3-01 · 나를 만나거나 내 전화를 받으시면 첫마디를 뭐라고 하셨어요?** 〔필수 · "기억이 안 나요" 허용〕  `단답 30자`

____________________

□ 실제로 들은 말은 아니에요

■ 기억이 안 나요

**M3-02 · 헤어지거나 전화를 끊을 때 마지막 말은요?** 〔필수 · "기억이 안 나요" 허용〕  `단답 30자`

<u>조심히 가라</u>

□ 기억이 안 나요

**M6-08 · 그분 하면 떠오르는 것 하나를 알려 주세요. 음식·물건·장소·냄새 무엇이든요.**  `단답 20자 + 단답 60자` 〔필수 · 2026-10-21 신설〕

| 떠오르는 것<br>단답 20자 | 왜 떠오르나요<br>단답 60자 · 선택 |
|---|---|
| 된장찌개 | 갈 때마다 끓여 주셨어요 |
| 낡은 라디오 |  |

> 가이드: 기억 DB로 들어간다.
"""


class IntakeSplitTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "Persona_Intake_시험.md"
        self.path.write_text(SHEET, encoding="utf-8")
        self.answers = parse(self.path)

    def test_headings_with_required_marks_are_read(self) -> None:
        a = self.answers
        self.assertEqual(a["C-01"].choice, "한두 문장으로 짧게")
        self.assertEqual(a["C-01"].fmt, "하나 선택")
        self.assertEqual(a["C-05"].fmt, "단답 40자")
        self.assertEqual(a["C-09"].fmt, "표 1~10행")
        self.assertEqual(a["M6-08"].fmt, "단답 20자 + 단답 60자")
        # 표시(〔…〕)가 다음 문항을 앞 문항 답으로 삼키지 않는다
        self.assertEqual(a["C-07"].checked, ["음식·밥을 챙겨서"])
        self.assertEqual(a["M3-02"].value, "조심히 가라")

    def test_forgot_mark_is_unknown_not_an_answer(self) -> None:
        m301 = self.answers["M3-01"]
        self.assertTrue(m301.unknown)
        self.assertIsNone(m301.choice)
        self.assertFalse(m301)
        intake = to_intake(self.answers, name="김순자")
        style = intake["intakeContext"]["speechStyle"]
        self.assertIsNone(style["callOpen"])
        self.assertEqual(style["callClose"], "조심히 가라")
        self.assertEqual(intake["intakeContext"]["personality"]["affectionStyle"], ["음식·밥을 챙겨서"])
        # 한 문장 소개(C-05)는 이제 선택이라 비어 있어도 된다
        self.assertIsNone(intake["intakeContext"]["basicProfile"]["oneLine"])
        text = json.dumps([intake, to_memories(self.answers)], ensure_ascii=False)
        for word in LEAKS:
            self.assertNotIn(word, text)

    def test_unknown_ticks_in_table_cells_are_empty(self) -> None:
        """종이 작성본의 가족 표(나 행 · 자기 호칭)에 '■ 따로 없었어요'·'■ 기억이 안 나요'를 체크해도 모름과 같다
        (체크리스트 4). 그 말이 자기 호칭·기억으로 새지 않는다."""
        sheet = """# PART 3. 표준 모듈

**M2-01 · 페르소나가 알아야 할 가족을 적어주세요.**  `표 0~10행`

| 이름 | 그분과의 관계 | 그분이 이 사람을 부르는 호칭 | 그분이 자기를 부르는 말 | 이 사람이 그분을 부르는 호칭 | 대화할 때 톤 | 말투 | 대화하나요 |
|---|---|---|---|---|---|---|---|
| 이정우 | 아들 | 정우야 | ______  ■ 따로 없었어요 | 엄마 |  | 반말 | 예 |
| 이수진 | 딸 | ______  ■ 기억이 안 나요 | 엄마가  □ 모름 | ______  ■ 모름 |  | 반말 | 예 |

**M3-01 · 나를 만나거나 내 전화를 받으시면 첫마디를 뭐라고 하셨어요?**  `단답 30자`

■ 따로 없었어요

**T1-03 · 마지막 시기에 곁에 있던 가족**  `표 0~5행`

| 인물 | 곁에 있었나요 | 메모 |
|---|---|---|
| 이정우 | ■ 기억이 안 나요 | 매일 왔어요 |
"""
        path = self.path.with_name("Persona_Intake_표.md")
        path.write_text(sheet, encoding="utf-8")
        answers = parse(path)
        self.assertTrue(answers["M3-01"].unknown)
        self.assertFalse(answers["M3-01"])
        family = to_intake(answers, name="김순자")["intakeContext"]["familyMap"]
        jeongwoo, sujin = family
        self.assertIsNone(jeongwoo["selfReference"])
        self.assertEqual(jeongwoo["addressTermTo"], "정우야")
        self.assertIsNone(sujin["addressTermTo"])
        self.assertEqual(sujin["selfReference"], "엄마가")
        self.assertIsNone(sujin["addressTermFrom"])
        memories = to_memories(answers)
        self.assertEqual([m["memoryText"] for m in memories if m["sourceQuestion"] == "T1-03"],
                         ["이정우 · 매일 왔어요"])
        text = json.dumps([to_intake(answers, name="김순자"), memories], ensure_ascii=False)
        for word in LEAKS + ("모름",):
            self.assertNotIn(word, text)

    def test_m6_08_becomes_memories(self) -> None:
        rows = [r for r in to_memories(self.answers) if r["sourceQuestion"] == "M6-08"]
        self.assertEqual(
            [r["memoryText"] for r in rows],
            [
                "가족이 나를 떠올리면 생각나는 것: 된장찌개 — 갈 때마다 끓여 주셨어요",
                "가족이 나를 떠올리면 생각나는 것: 낡은 라디오",
            ],
        )
        self.assertEqual(rows[0]["tags"], ["떠오르는 것"])
        self.assertEqual(rows[0]["usageScope"], "자유")
        self.assertEqual([r["id"] for r in rows], ["M6-08-1", "M6-08-2"])

    def test_the_intake_doc_parses_every_question(self) -> None:
        """docs/Persona_Intake.md(기획 기준본 사본)의 모든 문항 머리를 읽는다. 빈 양식은 답이 없다."""
        heads = [line for line in DOC.read_text(encoding="utf-8").splitlines()
                 if re.match(r"^\*\*[A-Z0-9-]+ · ", line)]
        self.assertTrue(heads)
        missed = [line for line in heads if not QID.match(line)]
        self.assertEqual(missed, [])
        answers = parse(DOC)
        for qid in ("P1-07A", "C-01", "C-05", "C-09", "M3-01", "M6-08", "M7-01", "M9-01"):
            self.assertIn(qid, answers)
        self.assertEqual(to_memories(answers), [])
        intake = json.dumps(to_intake(answers, name="대상자"), ensure_ascii=False)
        for word in LEAKS:
            self.assertNotIn(word, intake)


if __name__ == "__main__":
    unittest.main()
