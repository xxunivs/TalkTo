import unittest

from app.schemas.transcript import TranscriptSegment
from app.services.analysis.sensitivity import (
    SensitivityValidationError,
    apply_judge_decisions,
    validate_sensitivity_payload,
)


def segment(index: int) -> TranscriptSegment:
    return TranscriptSegment(
        segment_index=index,
        start_ms=index * 1000,
        end_ms=(index + 1) * 1000,
        speaker_label="SPK_0",
        transcript_text="말",
    )


SEGMENTS = [segment(0), segment(1), segment(2)]


class ValidateSensitivityPayloadTest(unittest.TestCase):
    def test_keeps_valid_flags_and_dedupes_ids(self) -> None:
        result = validate_sensitivity_payload(
            {
                "sensitivityFlags": [
                    {
                        "type": "health",
                        "description": "당뇨 투약 언급",
                        "sourceSegmentIds": [2, 0, 2],
                    }
                ]
            },
            SEGMENTS,
        )

        self.assertEqual(
            result["sensitivityFlags"],
            [
                {
                    "type": "health",
                    "description": "당뇨 투약 언급",
                    "sourceSegmentIds": [0, 2],
                }
            ],
        )

    def test_empty_flags_allowed(self) -> None:
        result = validate_sensitivity_payload({"sensitivityFlags": []}, SEGMENTS)

        self.assertEqual(result["sensitivityFlags"], [])

    def test_unknown_type_raises(self) -> None:
        with self.assertRaises(SensitivityValidationError):
            validate_sensitivity_payload(
                {
                    "sensitivityFlags": [
                        {"type": "romance", "description": "x", "sourceSegmentIds": [0]}
                    ]
                },
                SEGMENTS,
            )

    def test_fabricated_segment_ids_raise(self) -> None:
        with self.assertRaises(SensitivityValidationError):
            validate_sensitivity_payload(
                {
                    "sensitivityFlags": [
                        {
                            "type": "health",
                            "description": "근거 없음",
                            "sourceSegmentIds": [99],
                        }
                    ]
                },
                SEGMENTS,
            )

    def test_missing_description_raises(self) -> None:
        with self.assertRaises(SensitivityValidationError):
            validate_sensitivity_payload(
                {
                    "sensitivityFlags": [
                        {"type": "health", "description": " ", "sourceSegmentIds": [0]}
                    ]
                },
                SEGMENTS,
            )


class ApplyJudgeDecisionsTest(unittest.TestCase):
    flags = {
        "sensitivityFlags": [
            {"type": "health", "description": "당뇨", "sourceSegmentIds": [0]},
            {"type": "death", "description": "생일 미역국", "sourceSegmentIds": [1]},
        ]
    }

    def test_drops_only_judged_drop(self) -> None:
        result = apply_judge_decisions(
            self.flags,
            {
                "decisions": [
                    {"index": 0, "verdict": "keep", "reason": "부합"},
                    {"index": 1, "verdict": "drop", "reason": "생일 관습은 death 아님"},
                ]
            },
        )

        self.assertEqual(len(result["sensitivityFlags"]), 1)
        self.assertEqual(result["sensitivityFlags"][0]["type"], "health")
        self.assertEqual(len(result["judgeDropped"]), 1)

    def test_missing_or_malformed_decisions_keep_flags(self) -> None:
        result = apply_judge_decisions(
            self.flags,
            {"decisions": [{"index": "bad", "verdict": "drop"}, "garbage"]},
        )

        self.assertEqual(len(result["sensitivityFlags"]), 2)
        self.assertEqual(result["judgeDropped"], [])


if __name__ == "__main__":
    unittest.main()
