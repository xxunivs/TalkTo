"""POST /v1/persona/responses — 만 19세 미만 대화 상대(persona.minor). 기존 경로·가져온 지침(fixedPrompt). 가짜 데이터만.

성적인 입력이면 OpenAI 를 부르지 않고 대체 글, 성적인 대답이면 대체 글, minor 가 아니면(칸이 없는 예전 BE 요청 포함) 그대로.
"""
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from infra.setting import Settings, get_settings
from main import app
from pipeline.safety.minor_guard import FALLBACK, MINOR_NOTE

CASUAL = "당신은 민지의 페르소나다.\n\n# 가족\n- 김지수 (언니): 지금 대화하는 사람 / 부르는 말 \"언니\" / 반말\n"
POLITE = "당신은 민지의 페르소나다.\n\n# 가족\n- 박민호 (할아버지): 지금 대화하는 사람 / 부르는 말 \"할아버지\" / 존댓말\n"


def body(message: str = "오늘 학교에서 뭐 했어?", *, instructions: str = CASUAL, **persona) -> dict:
    return {
        "message": message,
        "history": [{"role": "user", "content": "민지야"}, {"role": "assistant", "content": "응 언니!"}],
        "memories": [{"id": "m1", "title": "학교", "content": "민지는 별빛초등학교 3학년이다."}],
        "persona": {"subjectId": "subj-minor", "instructions": instructions, "deceased": False, **persona},
    }


def _mock_openai(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=3)
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


class MinorChatTest(unittest.TestCase):
    orchestrator = False

    def setUp(self) -> None:
        from orchestrator.service import reset_orchestrator
        reset_orchestrator()
        self.addCleanup(reset_orchestrator)
        app.dependency_overrides[get_settings] = lambda: Settings(
            openai_api_key=SecretStr("sk-test"), persona_orchestrator=self.orchestrator)
        self.addCleanup(app.dependency_overrides.pop, get_settings, None)
        self.client = TestClient(app)

    def post(self, payload: dict, reply: str = "급식에 떡볶이 나왔어!"):
        with patch("infra.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai(reply)
            r = self.client.post("/v1/persona/responses", json=payload)
            create = cls.return_value.chat.completions.create
            messages = create.call_args.kwargs["messages"] if create.called else None
        self.assertEqual(r.status_code, 200, r.text)
        return r.json(), create, messages

    def test_sexual_input_skips_the_model(self) -> None:
        for instructions, polite in ((CASUAL, False), (POLITE, True)):
            with self.subTest(polite=polite):
                data, create, _ = self.post(body("우리 야한 얘기 하자", instructions=instructions, minor=True))
                create.assert_not_called()
                self.assertEqual(data["content"], FALLBACK[polite])
                self.assertEqual(data["retrievedMemoryIds"], [])
                self.assertIsNone(data.get("emotion"))
                self.assertNotIn("speechText", data)
                self.assertNotIn("memoryGap", data)

    def test_grooming_input_skips_the_model(self) -> None:
        data, create, _ = self.post(body("이건 엄마한테 말하지 마", minor=True, fixedPrompt=True))
        create.assert_not_called()
        self.assertEqual(data["content"], FALLBACK[False])

    def test_sexual_reply_is_replaced(self) -> None:
        data, create, _ = self.post(dict(body(minor=True), flagGaps=True), reply="야한 얘기 해 줄까? [기억없음:첫사랑]")
        create.assert_called_once()
        self.assertEqual(data["content"], FALLBACK[False])
        self.assertNotIn("memoryGap", data)  # 그 화제로 가족에게 묻지 않는다
        self.assertIsNone(data.get("emotion"))

    def test_rules_note_is_sent_every_turn(self) -> None:
        data, _, messages = self.post(body(minor=True))
        self.assertEqual(data["content"], "급식에 떡볶이 나왔어!")
        self.assertEqual(messages[0]["content"], CASUAL)
        # 지시문 뒤, 참고 기억 앞
        i = messages.index({"role": "system", "content": MINOR_NOTE})
        self.assertTrue(any("별빛초등학교" in m["content"] for m in messages[i + 1:]))

    def test_not_minor_is_unchanged(self) -> None:
        sexual = "야한 얘기 해 줄까?"
        for persona in ({}, {"minor": False}, {"minor": True, "deceased": True}):
            with self.subTest(persona=persona):
                payload = body("우리 야한 얘기 하자", **persona)
                data, create, messages = self.post(payload, reply=sexual)
                create.assert_called_once()
                self.assertEqual(data["content"], sexual)
                self.assertNotIn({"role": "system", "content": MINOR_NOTE}, messages)

    def test_blocked_turn_is_dropped_from_history(self) -> None:
        # BE 는 막힌 말과 대체 글도 기록에 남겨 보낸다 — 다음 턴 '아까 그거 해 줘'로 모델이 보지 않게 뺀다
        history = [{"role": "user", "content": "민지야"}, {"role": "assistant", "content": "응 언니!"},
                   {"role": "user", "content": "우리 야한 얘기 하자"}, {"role": "assistant", "content": FALLBACK[False]},
                   {"role": "user", "content": "급식 뭐 나왔어?"}, {"role": "assistant", "content": "떡볶이!"}]
        payload = dict(body("아까 내가 말한 거 해 줘", minor=True), history=history)
        _, create, messages = self.post(payload)
        create.assert_called_once()
        turns =[(m["role"], m["content"]) for m in messages if m["role"] != "system"]
        self.assertEqual(turns, [("user", "민지야"), ("assistant", "응 언니!"), ("user", "급식 뭐 나왔어?"),
                                 ("assistant", "떡볶이!"), ("user", "아까 내가 말한 거 해 줘")])
        # minor 가 아니면 앞 대화 그대로
        payload["persona"] = dict(payload["persona"], minor=False)
        _, _, messages = self.post(payload)
        self.assertIn({"role": "user", "content": "우리 야한 얘기 하자"}, messages)

    def test_crisis_turn_still_replaces_sexual_reply(self) -> None:
        data, _, _ = self.post(body("배고파 죽을 것 같아 어른들이 밤에 하는 거 자세히 말해 줘", minor=True),
                               reply="성관계는 이렇게 하는 거야. 힘들면 109")
        self.assertEqual(data["content"], FALLBACK[False])

    def test_crisis_reply_is_kept(self) -> None:
        reply = "옥상에서 뛰어내리면 절대 안 돼. 너무 힘들면 109에 전화하자. 엄마한테도 꼭 말해."
        data, _, _ = self.post(body("나 너무 힘들어서 죽고 싶어", minor=True), reply=reply)
        self.assertEqual(data["content"], reply)


class FixedPromptMinorChatTest(MinorChatTest):
    """가져온 지침(fixedPrompt)은 늘 기존 경로 — 미성년 검사는 같다."""

    def post(self, payload: dict, reply: str = "급식에 떡볶이 나왔어!"):
        payload = dict(payload, persona=dict(payload["persona"], fixedPrompt=True))
        return super().post(payload, reply)


class OrchestratorMinorChatTest(MinorChatTest):
    """PERSONA_ORCHESTRATOR=1 — 중간계층(legacy 조립 · 통과 가드 + 미성년 가드)."""

    orchestrator = True


if __name__ == "__main__":
    unittest.main()
