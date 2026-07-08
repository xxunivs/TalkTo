import unittest

from fastapi.testclient import TestClient

from app.main import app


class PersonaAssemblyTest(unittest.TestCase):
    """POST /v1/persona/assembly — LLM 미사용(순수 조립)이라 실호출 없이 검증 가능."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_assembly_builds_prompt_from_context(self) -> None:
        response = self.client.post(
            "/v1/persona/assembly",
            json={
                "subjectContext": {
                    "subject": {"name": "홍길동", "addressTerm": "할머니"},
                },
                "speechExamples": ["밥은 먹었냐?"],
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["subjectName"], "홍길동")
        # 대상자 이름과 발화 예시가 조립본에 반영되어야 한다.
        self.assertIn("홍길동", body["instructions"])
        self.assertIn("밥은 먹었냐?", body["instructions"])

    def test_assembly_death_context_not_leaked(self) -> None:
        # 사후 페르소나여도 사인·경위 원문은 조립본에 넣지 않는다(안전).
        response = self.client.post(
            "/v1/persona/assembly",
            json={
                "subjectContext": {"subject": {"name": "김순자", "addressTerm": "할머니"}},
                "intakeContext": {
                    "basicProfile": {
                        "status": "사망",
                        "deathContext": "2024년 폐렴으로 별세",
                    },
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        instructions = response.json()["instructions"]
        self.assertIn("사후 페르소나", instructions)
        self.assertNotIn("폐렴", instructions)


if __name__ == "__main__":
    unittest.main()
