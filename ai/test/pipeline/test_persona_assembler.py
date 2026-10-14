import unittest

from pipeline.s3_synthesis.c_build_system_prompt import (
    aggregate_taboo,
    assemble_persona_prompt,
    collect_speech_examples,
    merge_persons,
)
from infra.schema.subject import IntakeContext, SubjectContext
from infra.schema.transcript import TranscriptSegment


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


if __name__ == "__main__":
    unittest.main()
