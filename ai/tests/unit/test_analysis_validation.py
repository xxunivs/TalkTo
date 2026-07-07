import unittest

from app.schemas.transcript import TranscriptSegment
from app.services.analysis.persons import validate_persons_payload


def segment(index: int, text: str = "말") -> TranscriptSegment:
    return TranscriptSegment(
        segment_index=index,
        start_ms=index * 1000,
        end_ms=(index + 1) * 1000,
        speaker_label="SPK_0",
        transcript_text=text,
    )


SEGMENTS = [
    segment(0, "어, 종서니? 밥은 먹었냐"),
    segment(1, "걔가 회사 다니잖니"),
    segment(2, "영아, 잘 있었어"),
]


class ValidatePersonsPayloadTest(unittest.TestCase):
    def test_keeps_valid_person(self) -> None:
        result = validate_persons_payload(
            {
                "persons": [
                    {
                        "name": "종서",
                        "relationToSubject": "아들",
                        "mentions": ["종서니", "걔"],
                        "confidence": "confirmed",
                        "sourceSegmentIds": [0, 2],
                        "relationsToOthers": [],
                        "facts": [
                            {"fact": "회사 근무", "sourceSegmentIds": [1], "confidence": "inferred"}
                        ],
                    }
                ],
                "unresolvedMentions": [],
            },
            SEGMENTS,
        )

        self.assertEqual(len(result["persons"]), 1)
        self.assertEqual(result["persons"][0]["facts"][0]["fact"], "회사 근무")
        self.assertEqual(sum(result["validationDropped"].values()), 0)

    def test_drops_person_with_fabricated_segment_ids(self) -> None:
        result = validate_persons_payload(
            {
                "persons": [
                    {
                        "name": "유령",
                        "confidence": "confirmed",
                        "sourceSegmentIds": [99],
                    }
                ]
            },
            SEGMENTS,
        )

        self.assertEqual(result["persons"], [])
        self.assertEqual(result["validationDropped"]["persons"], 1)

    def test_drops_invalid_confidence_and_filters_bad_ids(self) -> None:
        result = validate_persons_payload(
            {
                "persons": [
                    {
                        "name": "종서",
                        "confidence": "maybe",
                        "sourceSegmentIds": [0],
                    },
                    {
                        "name": "향",
                        "confidence": "confirmed",
                        "sourceSegmentIds": [0, 99],
                        "mentions": ["영아"],
                        "facts": [
                            {"fact": "근거 없음", "sourceSegmentIds": [99], "confidence": "confirmed"}
                        ],
                    },
                ]
            },
            SEGMENTS,
        )

        self.assertEqual(len(result["persons"]), 1)
        self.assertEqual(result["persons"][0]["name"], "향")
        self.assertEqual(result["persons"][0]["sourceSegmentIds"], [0])
        self.assertEqual(result["persons"][0]["facts"], [])
        self.assertEqual(result["validationDropped"]["persons"], 1)
        self.assertEqual(result["validationDropped"]["facts"], 1)

    def test_drops_person_whose_mentions_are_not_in_transcript(self) -> None:
        result = validate_persons_payload(
            {
                "persons": [
                    {
                        "name": "이종서",
                        "confidence": "confirmed",
                        "sourceSegmentIds": [0],
                        "mentions": ["종서야"],
                    }
                ]
            },
            SEGMENTS,
        )

        self.assertEqual(result["persons"], [])
        self.assertEqual(result["validationDropped"]["persons"], 1)
        self.assertEqual(result["validationDropped"]["mentions"], 1)

    def test_keeps_person_with_transcript_surface_mention(self) -> None:
        result = validate_persons_payload(
            {
                "persons": [
                    {
                        "name": "남향",
                        "confidence": "inferred",
                        "sourceSegmentIds": [2],
                        "mentions": ["영아", "향아"],
                    }
                ]
            },
            SEGMENTS,
        )

        self.assertEqual(len(result["persons"]), 1)
        self.assertEqual(result["persons"][0]["mentions"], ["영아"])
        self.assertEqual(result["validationDropped"]["mentions"], 1)

    def test_drops_subject_self_entries(self) -> None:
        result = validate_persons_payload(
            {
                "persons": [
                    {
                        "name": "신금자",
                        "relationToSubject": None,
                        "confidence": "confirmed",
                        "sourceSegmentIds": [0],
                        "mentions": ["걔"],
                    },
                    {
                        "name": "할머니",
                        "relationToSubject": "본인",
                        "confidence": "confirmed",
                        "sourceSegmentIds": [0],
                        "mentions": ["걔"],
                    },
                ]
            },
            SEGMENTS,
            subject_name="신금자",
        )

        self.assertEqual(result["persons"], [])
        self.assertEqual(result["validationDropped"]["persons"], 2)

    def test_unresolved_requires_mention_and_ids(self) -> None:
        result = validate_persons_payload(
            {
                "unresolvedMentions": [
                    {"mention": "그 양반", "context": "불명", "sourceSegmentIds": [1]},
                    {"mention": "", "sourceSegmentIds": [1]},
                    {"mention": "걔", "sourceSegmentIds": []},
                ]
            },
            SEGMENTS,
        )

        self.assertEqual(len(result["unresolvedMentions"]), 1)
        self.assertEqual(result["validationDropped"]["unresolved"], 2)


if __name__ == "__main__":
    unittest.main()
