import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import app
from app.pipeline.persona.service import extract_memory_candidates
from app.schemas.persona import MemoryCandidateRequest


def _mock_openai_returning(content: str) -> MagicMock:
    """OpenAI 클라이언트 목 — chat.completions.create가 정해진 content를 반환."""
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


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


class MemoryCandidateTest(unittest.TestCase):
    """extract_memory_candidates — LLM 응답을 목킹해 파싱·범위 강제 로직만 검증(무API)."""

    def _settings(self) -> Settings:
        return Settings(openai_api_key=SecretStr("test-key"))

    @patch("app.providers.llm.OpenAI")
    def test_clamps_out_of_range_and_skips_empty(self, mock_openai: MagicMock) -> None:
        canned = json.dumps({
            "candidates": [
                # 범위를 벗어난 값 — 코드가 1~10 / 0~1로 강제해야 스키마 검증에서 안 터진다.
                {"summary": "사용자가 취직했다.", "category": "직장",
                 "importance": 15, "confidence": 2.0},
                # summary 빈 값 — 건너뛰어야 한다.
                {"summary": "  ", "importance": 5, "confidence": 0.5},
            ]
        })
        mock_openai.return_value = _mock_openai_returning(canned)

        result = extract_memory_candidates(
            MemoryCandidateRequest(user_message="나 취직했어", assistant_message="잘됐다"),
            settings=self._settings(),
        )

        self.assertEqual(len(result.candidates), 1)
        c = result.candidates[0]
        self.assertEqual(c.summary, "사용자가 취직했다.")
        self.assertEqual(c.importance, 10)
        self.assertEqual(c.confidence, 1.0)

    @patch("app.providers.llm.OpenAI")
    def test_empty_candidates_when_nothing_to_remember(self, mock_openai: MagicMock) -> None:
        mock_openai.return_value = _mock_openai_returning('{"candidates": []}')

        result = extract_memory_candidates(
            MemoryCandidateRequest(user_message="보고 싶어", assistant_message="나도"),
            settings=self._settings(),
        )

        self.assertEqual(result.candidates, [])


if __name__ == "__main__":
    unittest.main()
