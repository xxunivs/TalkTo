"""기억의 '다른 문자 섞임' 검사(ai-correct-5): 글자만 본다. 문장부호·기호는 멀쩡한 기억이다."""

import unittest

from app.schema.transcript import TranscriptSegment
from pipeline.s2_extraction.e_extract_memory import has_foreign_script, validate_memory_payload

SEGMENTS = [TranscriptSegment(segment_index=0, start_ms=0, end_ms=1000, speaker_label="SPK_0",
                              transcript_text="밥은 먹었냐")]


class ForeignScriptTest(unittest.TestCase):
    def test_punctuation_and_symbols_are_kept(self) -> None:
        for text in (
            "대상자는 전화할 때마다 “밥은 먹었냐”고 묻는다",
            "‘아이고’라는 말을 자주 한다",
            "3/1절에 태극기를 단다",
            "김치&된장을 보냈다",
            "5°C 아래로 내려가면 장독을 덮는다",
            "서울ㆍ부산을 오갔다",
            "50+ 모임에 나간다",
            "A/S 를 맡겼다",
            "카페(café)에 갔다",
            "정상 문장 (50%) 이다.",
        ):
            self.assertFalse(has_foreign_script(text), text)

    def test_other_scripts_are_still_dropped(self) -> None:
        for text in (
            "통화 শেষে 인사했다",  # 벵골어
            "그는 很 좋아했다",  # 한자 섞임
            "ありがとう라고 했다",  # 가나
            "Привет 라고 했다",  # 키릴
            "สวัสดี 했다",  # 태국어
        ):
            self.assertTrue(has_foreign_script(text), text)

    def test_quoted_saying_survives_validation(self) -> None:
        result = validate_memory_payload(
            {"memorySegments": [
                {"memoryText": "대상자는 “밥은 먹었냐”고 묻는다", "sourceSegmentIds": [0], "confidence": "confirmed"},
            ]},
            SEGMENTS,
        )
        self.assertEqual(len(result["memorySegments"]), 1)
        self.assertEqual(result["validationDropped"]["memories"], 0)


if __name__ == "__main__":
    unittest.main()
