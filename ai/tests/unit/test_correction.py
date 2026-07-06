import unittest

from app.core.config import Settings
from app.schemas.transcript import TranscriptSegment
from app.services.correction import apply_corrections, correct_segments


def segment(index: int, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        segment_index=index,
        start_ms=index * 1000,
        end_ms=(index + 1) * 1000,
        speaker_label="SPK_0",
        transcript_text=text,
    )


class ApplyCorrectionsTest(unittest.TestCase):
    def test_fills_corrected_text_and_keeps_original(self) -> None:
        chunk = [segment(0, "영아, 밥은 먹었냐")]

        apply_corrections(
            chunk,
            {"corrections": [{"segmentIndex": 0, "correctedText": "향아, 밥은 먹었냐"}]},
        )

        self.assertEqual(chunk[0].corrected_text, "향아, 밥은 먹었냐")
        self.assertEqual(chunk[0].transcript_text, "영아, 밥은 먹었냐")
        self.assertFalse(chunk[0].needs_review)

    def test_marks_needs_review(self) -> None:
        chunk = [segment(0, "그 뭐냐 거기"), segment(1, "정업인가 정읍인가")]

        apply_corrections(chunk, {"corrections": [], "needsReview": [1]})

        self.assertFalse(chunk[0].needs_review)
        self.assertTrue(chunk[1].needs_review)

    def test_rejects_rewrite_beyond_length_bounds(self) -> None:
        chunk = [segment(0, "짧은 말")]

        apply_corrections(
            chunk,
            {
                "corrections": [
                    {
                        "segmentIndex": 0,
                        "correctedText": "완전히 다시 쓴 훨씬 길어진 문장이라 의미 변경이 의심됨",
                    }
                ]
            },
        )

        self.assertIsNone(chunk[0].corrected_text)
        self.assertTrue(chunk[0].needs_review)

    def test_ignores_unchanged_unknown_or_malformed_entries(self) -> None:
        chunk = [segment(0, "밥은 먹었냐")]

        apply_corrections(
            chunk,
            {
                "corrections": [
                    {"segmentIndex": 0, "correctedText": "밥은 먹었냐"},
                    {"segmentIndex": 99, "correctedText": "없는 세그먼트"},
                    {"segmentIndex": 0, "correctedText": ""},
                    "garbage",
                ],
                "needsReview": [99],
            },
        )

        self.assertIsNone(chunk[0].corrected_text)
        self.assertFalse(chunk[0].needs_review)


class PhoneticGuardTest(unittest.TestCase):
    def test_accepts_phonetically_similar_name_fix(self) -> None:
        chunk = [segment(0, "어, 영아.")]

        apply_corrections(
            chunk, {"corrections": [{"segmentIndex": 0, "correctedText": "어, 향아."}]}
        )

        self.assertEqual(chunk[0].corrected_text, "어, 향아.")

    def test_rejects_phonetically_distant_swap(self) -> None:
        chunk = [segment(0, "구려유. 할머니도 잘 자.")]

        apply_corrections(
            chunk,
            {
                "corrections": [
                    {"segmentIndex": 0, "correctedText": "향아. 할머니도 잘 자."}
                ]
            },
        )

        self.assertIsNone(chunk[0].corrected_text)
        self.assertTrue(chunk[0].needs_review)

    def test_rejects_borderline_dialect_swap(self) -> None:
        chunk = [segment(0, "구려유. 할머니도 잘 자.")]

        apply_corrections(
            chunk,
            {
                "corrections": [
                    {"segmentIndex": 0, "correctedText": "규하유. 할머니도 잘 자."}
                ]
            },
        )

        self.assertIsNone(chunk[0].corrected_text)
        self.assertTrue(chunk[0].needs_review)

    def test_rejects_token_count_change(self) -> None:
        chunk = [segment(0, "그려유 할머니")]

        apply_corrections(
            chunk,
            {"corrections": [{"segmentIndex": 0, "correctedText": "그려유 우리 할머니"}]},
        )

        self.assertIsNone(chunk[0].corrected_text)
        self.assertTrue(chunk[0].needs_review)


class CorrectSegmentsSkipTest(unittest.TestCase):
    def test_empty_glossary_skips_pass(self) -> None:
        chunk = [segment(0, "영아, 밥은 먹었냐")]

        result = correct_segments(chunk, glossary=[], settings=Settings())

        self.assertIsNone(result[0].corrected_text)

    def test_missing_api_key_skips_pass(self) -> None:
        chunk = [segment(0, "영아, 밥은 먹었냐")]

        result = correct_segments(
            chunk, glossary=["향"], settings=Settings(openai_api_key=None)
        )

        self.assertIsNone(result[0].corrected_text)


if __name__ == "__main__":
    unittest.main()
