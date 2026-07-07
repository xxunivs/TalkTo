import unittest

from app.pipeline.memory_segments.service import validate_memory_payload
from app.schemas.transcript import TranscriptSegment


def segment(index: int, speaker: str = "SPK_0") -> TranscriptSegment:
    return TranscriptSegment(
        segment_index=index,
        start_ms=index * 1000,
        end_ms=(index + 1) * 1000,
        speaker_label=speaker,
        transcript_text="말",
    )


SEGMENTS = [segment(0), segment(1, "SPK_1"), segment(2)]
SENSITIVITY = {"sensitivityFlags": [{"type": "health", "description": "x", "sourceSegmentIds": [2]}]}
# 기억이 [0,2]를 인용하면 중간 1번의 플래그도 범위 조인으로 잡혀야 한다
SENSITIVITY_MID = {"sensitivityFlags": [{"type": "asset", "description": "y", "sourceSegmentIds": [1]}]}


class ValidateMemoryPayloadTest(unittest.TestCase):
    def test_derives_time_speaker_and_flags(self) -> None:
        result = validate_memory_payload(
            {
                "memorySegments": [
                    {
                        "memoryText": "대상자는 병원에 다녀왔다",
                        "sourceSegmentIds": [0, 2],
                        "relatedPeople": [],
                        "confidence": "confirmed",
                    }
                ]
            },
            SEGMENTS,
            SENSITIVITY,
        )

        memory = result["memorySegments"][0]
        self.assertEqual(memory["startMs"], 0)
        self.assertEqual(memory["endMs"], 3000)
        self.assertEqual(memory["speakerLabel"], "SPK_0")
        self.assertEqual(memory["sensitivityFlags"], ["health"])

    def test_joins_flag_within_cited_span(self) -> None:
        result = validate_memory_payload(
            {
                "memorySegments": [
                    {
                        "memoryText": "범위 조인 확인",
                        "sourceSegmentIds": [0, 2],
                        "confidence": "confirmed",
                    }
                ]
            },
            SEGMENTS,
            SENSITIVITY_MID,
        )

        # 1번은 인용 안 했지만 [0,2] 범위 안이므로 asset 플래그가 조인돼야 함
        self.assertEqual(result["memorySegments"][0]["sensitivityFlags"], ["asset"])

    def test_drops_fabricated_ids_and_bad_confidence(self) -> None:
        result = validate_memory_payload(
            {
                "memorySegments": [
                    {"memoryText": "근거 없음", "sourceSegmentIds": [99], "confidence": "confirmed"},
                    {"memoryText": "확신 애매", "sourceSegmentIds": [0], "confidence": "maybe"},
                ]
            },
            SEGMENTS,
        )

        self.assertEqual(result["memorySegments"], [])
        self.assertEqual(result["validationDropped"]["memories"], 2)

    def test_dedupes_normalized_text(self) -> None:
        result = validate_memory_payload(
            {
                "memorySegments": [
                    {"memoryText": "대상자는 빵을 먹는다.", "sourceSegmentIds": [0], "confidence": "confirmed"},
                    {"memoryText": "대상자는 빵을 먹는다", "sourceSegmentIds": [1], "confidence": "confirmed"},
                ]
            },
            SEGMENTS,
        )

        self.assertEqual(len(result["memorySegments"]), 1)
        self.assertEqual(result["validationDropped"]["duplicates"], 1)


if __name__ == "__main__":
    unittest.main()


class ForeignScriptGateTest(unittest.TestCase):
    def test_drops_memory_with_foreign_script(self) -> None:
        result = validate_memory_payload(
            {
                "memorySegments": [
                    {"memoryText": "통화 শেষে 인사했다", "sourceSegmentIds": [0], "confidence": "confirmed"},
                    {"memoryText": "정상 문장 (50%) 이다.", "sourceSegmentIds": [1], "confidence": "confirmed"},
                ]
            },
            SEGMENTS,
        )

        self.assertEqual(len(result["memorySegments"]), 1)
        self.assertEqual(result["validationDropped"]["memories"], 1)


class RelatedPeopleGateTest(unittest.TestCase):
    def test_excludes_subject_from_related_people(self) -> None:
        result = validate_memory_payload(
            {
                "memorySegments": [
                    {
                        "memoryText": "손주가 감사 인사를 했다",
                        "sourceSegmentIds": [0],
                        "relatedPeople": ["이준혁", "신금자"],
                        "confidence": "confirmed",
                    }
                ]
            },
            SEGMENTS,
            subject_name="신금자",
        )

        self.assertEqual(result["memorySegments"][0]["relatedPeople"], ["이준혁"])
