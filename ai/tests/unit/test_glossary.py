import unittest

from app.schemas.context import IntakeContext, SubjectContext
from app.pipeline.correction.glossary import build_glossary


class BuildGlossaryTest(unittest.TestCase):
    def test_extracts_names_address_terms_and_glossary(self) -> None:
        context = SubjectContext(
            **{
                "subject": {"addressTerm": "할머니", "name": "김영희"},
                "familyMembers": [
                    {
                        "name": "박철수",
                        "relationToSubject": "아들",
                        "addressTerms": ["철수야", "철수니?"],
                    }
                ],
                "glossaryTerms": ["부산", "돼지국밥"],
            }
        )

        terms = build_glossary(context)

        self.assertEqual(
            terms,
            ["김영희", "영희", "영희야", "박철수", "철수", "철수야", "철수니", "부산", "돼지국밥"],
        )

    def test_merges_intake_names_without_duplicates(self) -> None:
        subject = SubjectContext(
            **{"familyMembers": [{"name": "박철수"}], "glossaryTerms": []}
        )
        intake = IntakeContext(
            **{"sttHints": {"names": ["박철수", "이영자"], "voiceSampleRef": None}}
        )

        terms = build_glossary(subject, intake)

        self.assertEqual(terms, ["박철수", "철수", "철수야", "이영자", "영자", "영자야"])

    def test_handles_missing_contexts(self) -> None:
        self.assertEqual(build_glossary(None, None), [])
        self.assertEqual(build_glossary(SubjectContext()), [])

    def test_ignores_unknown_intake_sections(self) -> None:
        intake = IntakeContext(
            **{
                "basicProfile": {"birthYear": 1941},
                "tabooTopics": ["상속"],
                "sttHints": {"names": ["김영희"]},
            }
        )

        self.assertEqual(build_glossary(None, intake), ["김영희", "영희", "영희야"])


if __name__ == "__main__":
    unittest.main()
