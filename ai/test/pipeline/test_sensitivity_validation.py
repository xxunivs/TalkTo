import unittest

from infra.schema.transcript import TranscriptSegment
from pipeline.s2_extraction.b_extract_sensitivity import (
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

    def test_drop_log_lists_types_only(self) -> None:
        # 판정 이유·설명은 원문을 담을 수 있어 로그에 남기지 않는다(결과에는 남긴다)
        with self.assertLogs(
            "pipeline.s2_extraction.b_extract_sensitivity", level="INFO"
        ) as logs:
            result = apply_judge_decisions(
                self.flags,
                {"decisions": [{"index": 1, "verdict": "drop", "reason": "생일 미역국은 death 아님"}]},
            )
        text = "\n".join(logs.output)
        self.assertIn("death", text)
        self.assertNotIn("미역국", text)
        self.assertEqual(result["judgeDropped"][0]["judgeReason"], "생일 미역국은 death 아님")


class ContentFreeValidationErrorTest(unittest.TestCase):
    """구조 오류 문구는 로그로 간다. LLM 설명·받은 값 원문은 넣지 않는다(몇 번째·자료형·개수만)."""

    SECRET = "할머니 당뇨 투약"

    def _message(self, flag: object) -> str:
        with self.assertRaises(SensitivityValidationError) as caught:
            validate_sensitivity_payload({"sensitivityFlags": [flag]}, SEGMENTS)
        return str(caught.exception)

    def test_messages_do_not_contain_content(self) -> None:
        cases = [
            self.SECRET,  # 객체가 아님
            {"type": self.SECRET, "description": self.SECRET, "sourceSegmentIds": [0]},
            {"type": "health", "description": self.SECRET, "sourceSegmentIds": []},
            {"type": "health", "description": self.SECRET, "sourceSegmentIds": [99, self.SECRET]},
        ]
        for flag in cases:
            message = self._message(flag)
            self.assertNotIn(self.SECRET, message)
            self.assertNotIn("99", message)
            self.assertIn("flag 0", message)


if __name__ == "__main__":
    unittest.main()
