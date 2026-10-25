"""2026-10-21 만들 때 질문 개편 뒤 BE(intake.build_intake)가 보내는 모양으로 조립한다.

- 한 문장 소개(C-05)는 선택이 되어 비어 온다 → 두 조립기 모두 빈 칸 없이 그린다.
- C-03·C-07·C-09 는 '나에게(지금 대화하는 사람에게)' 하던 것이다 → 기존 문구로 어색하지 않게 들어간다.
- 첫마디(M3-01)를 '기억이 안 나요'로 고르면 BE 가 빼서 보낸다(칸이 비어 온다).
"""

import unittest

from pydantic import SecretStr

from infra.schema.persona import PersonaAssemblyRequest
from infra.setting import Settings
from pipeline.s3_synthesis.c_build_system_prompt import assemble_persona_instructions

# BE intake.build_intake 의 모양(개편 뒤 가족·고인, C-05·M3-01 없음)
REQUEST = {
    "subjectContext": {
        "subject": {"name": "김순자", "addressTerm": "할머니"},
        "familyMembers": [{"name": "홍길동", "relationToSubject": "손자", "addressTerms": ["길동아"]}],
        "glossaryTerms": ["김순자", "홍길동"],
    },
    "intakeContext": {
        "basicProfile": {"status": "사망", "relation": "할머니", "oneLine": None, "period": None,
                         "introduction": None, "deathContext": None},
        "audience": "나와 내가 초대한 사람만",
        "speechStyle": {"length": "한두 문장으로 짧게", "manner": "결론이나 답을 바로 말한다",
                        "dialect": None, "whenAngry": None, "interjections": [], "avoidStyles": [],
                        "sentenceEndings": [], "callOpen": None, "callClose": "조심히 가라"},
        "speechExamples": [{"text": "밥은 먹었냐", "inferred": False, "sourceQuestion": "C-09"}],
        "personality": {"oneLine": None, "introduction": None, "traits": ["다정함"], "values": [],
                        "affectionStyle": ["음식·밥을 챙겨서"],
                        "comfortStyle": "말없이 곁에 있어준다", "adviceStyle": [],
                        "conflictStyle": None},
        "familyMap": [{"name": "홍길동", "relation": "손자", "addressTermTo": "길동아",
                       "addressTermFrom": "할머니", "selfReference": "할머니가",
                       "speechLevel": "반말", "talksWithPersona": True, "isListener": True}],
        "situationalReactions": [],
        "tabooTopics": [{"topic": "그분의 사망·장례·임종", "grade": "물으면만",
                         "sourceQuestion": "M9-01"}],
    },
    "speechExamples": ["밥은 먹었냐"],
    "personaInsights": [],
}


class IntakeV2PromptTest(unittest.TestCase):
    def _both(self) -> dict[str, str]:
        req = PersonaAssemblyRequest.model_validate(REQUEST)
        s = Settings(openai_api_key=SecretStr("sk-test"))
        return {
            "layered": assemble_persona_instructions(
                req, s.model_copy(update={"persona_prompt_builder": "layered"})).instructions,
            "legacy": assemble_persona_instructions(
                req, s.model_copy(update={"persona_prompt_builder": "legacy"})).instructions,
        }

    def test_empty_one_line_and_call_open_render_cleanly(self) -> None:
        for builder, text in self._both().items():
            with self.subTest(builder=builder):
                self.assertNotIn("None", text)
                self.assertNotIn("한 줄 소개", text)  # 빈 C-05 는 줄째 빠진다(legacy)
                self.assertNotIn("전화 첫마디", text)  # 빈 M3-01 도 줄째 빠진다
                self.assertIn("밥은 먹었냐", text)  # 나에게 자주 하시던 말(C-09)
                self.assertIn("음식·밥을 챙겨서", text)  # 나를 챙기시던 방식(C-07)
                self.assertIn("말없이 곁에 있어준다", text)  # 내가 힘들어할 때(C-03)
        layered = self._both()["layered"]
        self.assertIn("- 이름: 김순자\n", layered)  # 한 줄 소개가 없으면 ' — ' 도 붙지 않는다


if __name__ == "__main__":
    unittest.main()
