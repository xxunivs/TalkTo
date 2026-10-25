import unittest

from app.schema.subject import IntakeContext, SubjectContext
from app.schema.transcript import TranscriptSegment
from pipeline.s3_synthesis.c_build_system_prompt import (
    aggregate_taboo,
    assemble_persona_prompt,
    collect_speech_examples,
    merge_persons,
)


class MergePersonsTest(unittest.TestCase):
    def test_merges_by_name_and_picks_confirmed_relation(self) -> None:
        results = [
            {"persons": [{"name": "이정우", "relationToSubject": "아들", "confidence": "confirmed", "mentions": ["정우야"]}]},
            {"persons": [{"name": "이정우", "relationToSubject": "손자", "confidence": "inferred", "mentions": ["정우"]}]},
        ]
        merged = merge_persons(results)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["relation"], "아들")  # confirmed 가중 우위


class SpeechExamplesTest(unittest.TestCase):
    def _seg(self, i, text, spk="SPK_0"):
        return TranscriptSegment(segment_index=i, start_ms=i, end_ms=i + 1, speaker_label=spk, transcript_text=text)

    def test_only_subject_short_utterances(self) -> None:
        segs = [self._seg(0, "짧은 대상자 발화야", "SPK_0"), self._seg(1, "상대 발화", "SPK_1"),
                self._seg(2, "너무 길어서 제외될 " + "가" * 50, "SPK_0")]
        ex = collect_speech_examples([segs], ["SPK_0"], limit=5)
        self.assertIn("짧은 대상자 발화야", ex)
        self.assertNotIn("상대 발화", ex)
        self.assertTrue(all(len(e) <= 40 for e in ex))


class TabooTest(unittest.TestCase):
    def test_aggregates_flag_types(self) -> None:
        results = [{"sensitivityFlags": [{"type": "health"}, {"type": "asset"}]}]
        taboo = aggregate_taboo(results)
        self.assertTrue(any("건강" in t for t in taboo))
        self.assertTrue(any("돈" in t for t in taboo))


class FamilyLineTest(unittest.TestCase):
    def test_family_line_keeps_how_the_subject_called_the_user(self) -> None:
        prompt = assemble_persona_prompt(
            subject_context=SubjectContext.model_validate({"subject": {"name": "박순자", "addressTerm": "할머니"}}),
            persons_results=[],
            sensitivity_results=[],
            segments_by_recording=[],
            subject_labels=[],
            intake_context=IntakeContext.model_validate(
                {"familyMap": [{"name": "김하늘", "relation": "손녀", "addressTermTo": "우리 강아지"}]}
            ),
        )
        self.assertIn('- 김하늘 (손녀) — 이 사람을 "우리 강아지"라고 부른다', prompt)


# 웹앱 가족 표: 첫 행이 지금 대화하는 사람, 나머지는 그 사람 기준 호칭·생존·메모를 더 보낸다
LISTENER_ROWS = [
    {"name": "홍길동", "relation": "손자", "addressTermTo": "길동아", "addressTermFrom": "할머니", "tone": "…",
     "talksWithPersona": True, "isListener": True},
    {"name": "홍철수", "relation": "큰아들", "addressTermTo": "철수야", "listenerCalls": "아빠", "deceased": False,
     "note": "막내라 늘 걱정하셨어요"},
    {"name": "홍영감", "relation": "남편", "listenerCalls": "할아버지", "deceased": True},
]
NEW_KEYS = ("isListener", "listenerCalls", "deceased", "note")
OLD_ROWS = [{k: v for k, v in row.items() if k not in NEW_KEYS} for row in LISTENER_ROWS]
FAMILY_HEADING = "# 가족 (사용자에 따라 아래 톤으로 응답)"
# 바꾸기 전 코드로 OLD_ROWS 를 조립해 얻은 그대로(새 칸이 없으면 한 글자도 달라지면 안 된다)
OLD_FAMILY_BLOCK = (
    FAMILY_HEADING + "\n"
    '- 홍길동 (손자) — 이 사람을 "길동아"라고 부른다: …\n'
    '- 홍철수 (큰아들) — 이 사람을 "철수야"라고 부른다\n'
    "- 홍영감 (남편)\n"
)
OLD_IDENTITY = "# 기본 정보\n김순자. 가족들은 '할머니'라고 부른다.\n"


def _listener_prompt(rows, subject=None) -> str:
    return assemble_persona_prompt(
        subject_context=SubjectContext.model_validate(
            {"subject": subject or {"name": "김순자", "addressTerm": "할머니"}}),
        persons_results=[],
        sensitivity_results=[],
        segments_by_recording=[],
        subject_labels=[],
        intake_context=IntakeContext.model_validate(
            {"basicProfile": {"status": "사망"}, "audience": "나와 내가 초대한 사람만", "familyMap": rows}),
    )


def _family_block(prompt: str) -> str:
    start = prompt.index(FAMILY_HEADING)
    return prompt[start:prompt.index("\n# 말투", start)]


class ListenerFamilyTest(unittest.TestCase):
    def test_old_payload_renders_exactly_as_before(self) -> None:
        prompt = _listener_prompt(OLD_ROWS)
        self.assertEqual(_family_block(prompt), OLD_FAMILY_BLOCK)
        self.assertIn(OLD_IDENTITY, prompt)

    def test_falsy_new_fields_render_like_old_payload(self) -> None:
        rows = [dict(row, isListener=False, deceased=False, listenerCalls="", note=" ") for row in OLD_ROWS]
        self.assertEqual(_listener_prompt(rows), _listener_prompt(OLD_ROWS))

    def test_listener_marker_and_identity_line(self) -> None:
        prompt = _listener_prompt(LISTENER_ROWS)
        self.assertIn("# 기본 정보\n김순자. 지금 대화하는 사람(홍길동)은 '할머니'라고 부른다.\n", prompt)
        self.assertNotIn("가족들은 '할머니'", prompt)
        self.assertIn('\n- 홍길동 (손자) (지금 대화하는 사람) — 이 사람을 "길동아"라고 부른다: …\n', prompt)

    def test_listener_calls_deceased_and_note(self) -> None:
        block = _family_block(_listener_prompt(LISTENER_ROWS))
        self.assertIn('\n- 홍철수 (큰아들) — 이 사람을 "철수야"라고 부른다 · 사용자는 "아빠"라고 부름'
                      " · 메모: 막내라 늘 걱정하셨어요\n", block)
        self.assertIn('\n- 홍영감 (남편) (돌아가심) · 사용자는 "할아버지"라고 부름\n', block)
        self.assertNotIn("홍철수 (큰아들) (돌아가심)", block)  # deceased=false 는 표시하지 않는다

    def test_guide_line_sits_under_heading_and_list_stays_people_only(self) -> None:
        lines = _family_block(_listener_prompt(LISTENER_ROWS)).strip().split("\n")
        self.assertEqual(lines[0], FAMILY_HEADING)
        guide = lines[1]
        self.assertFalse(guide.startswith("- "))
        self.assertIn("'지금 대화하는 사람'으로 적힌 사람이 지금 대화하는 상대다", guide)
        self.assertIn("'사용자는 …라고 부름'을 보고", guide)
        self.assertIn("연락해 보라고 하거나 연락할 사람으로 짚지 않는다", guide)
        self.assertEqual([ln.split(" (")[0] for ln in lines[2:]], ["- 홍길동", "- 홍철수", "- 홍영감"])

    def test_guide_only_mentions_what_is_in_the_table(self) -> None:
        rows = [dict(OLD_ROWS[1]), dict(OLD_ROWS[2], deceased=True)]
        block = _family_block(_listener_prompt(rows))
        self.assertIn("'돌아가심'으로 적힌 분은", block)
        self.assertNotIn("지금 대화하는 상대", block)
        self.assertNotIn("사용자는", block)

    def test_listener_without_address_term_from_keeps_family_wording(self) -> None:
        rows = [dict(LISTENER_ROWS[0], addressTermFrom=None)] + LISTENER_ROWS[1:]
        self.assertIn(OLD_IDENTITY, _listener_prompt(rows))

    def test_listener_identity_particle_and_no_subject_address(self) -> None:
        rows = [dict(LISTENER_ROWS[0], addressTermFrom="할머님")] + LISTENER_ROWS[1:]
        prompt = _listener_prompt(rows, subject={"name": "김순자"})
        self.assertIn("김순자. 지금 대화하는 사람(홍길동)은 '할머님'이라고 부른다.", prompt)

    def test_schema_aliases_and_one_line_cleanup(self) -> None:
        note = IntakeContext.model_validate({"familyMap": [
            {"name": "홍철수", "listenerCalls": " 아빠 ", "deceased": True, "isListener": False,
             "note": "막내라\n늘  걱정"}]}).family_map[0]
        self.assertEqual((note.listener_calls, note.deceased, note.is_listener, note.note),
                         ("아빠", True, False, "막내라 늘 걱정"))


if __name__ == "__main__":
    unittest.main()
