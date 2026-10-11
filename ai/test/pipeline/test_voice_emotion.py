import unittest
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from infra.schema.persona import PersonaResponseRequest
from infra.setting import Settings
from pipeline.s3_synthesis.c_build_system_prompt import generate_persona_response
from pipeline.s4_voice.b_emotion import split_emotion


def _openai_returning(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


class SplitEmotionTest(unittest.TestCase):
    """대답 맨 앞 [감정:이름] 을 떼고, 고인 안전선을 적용한다."""

    def test_leading_tag_is_removed_from_text(self) -> None:
        emotion, text = split_emotion("[감정:happy] 아이고 잘됐다!", deceased=False)
        self.assertEqual((emotion, text), ("happy", "아이고 잘됐다!"))

    def test_stray_tags_are_removed_but_not_used(self) -> None:
        emotion, text = split_emotion("그래. [감정:sad] 보고 싶다.", deceased=False)
        self.assertIsNone(emotion)
        self.assertNotIn("감정", text)

    def test_unknown_or_forbidden_names_are_dropped(self) -> None:
        for name in ("angry", "fearful", "crying"):
            emotion, text = split_emotion(f"[감정:{name}] 뭐라고?", deceased=False)
            self.assertIsNone(emotion)
            self.assertEqual(text, "뭐라고?")

    def test_deceased_sadness_is_capped_to_calm(self) -> None:
        self.assertEqual(split_emotion("[감정:sad] 그래…", deceased=True)[0], "calm")
        # 고인 여부를 모르면 고인으로 본다
        self.assertEqual(split_emotion("[감정:sad] 그래…", deceased=None)[0], "calm")
        self.assertEqual(split_emotion("[감정:sad] 그래…", deceased=False)[0], "sad")

    def test_other_tag_shapes_are_removed(self) -> None:
        # 실제로 [sad] 만 붙여 나와 소리로 읽힌 적이 있다(2026-10-10)
        for raw, want in (("[sad] 준혁아, 보고 싶다.", "sad"), ("(감정: 기쁨) 잘됐다!", "happy"),
                          ("[emotion: Calm] 괜찮아.", "calm"), ("【감정：happy】 그래!", "happy")):
            emotion, text = split_emotion(raw, deceased=False)
            self.assertEqual(emotion, want, raw)
            self.assertFalse(any(ch in text for ch in "[](（【"), text)

    def test_other_parentheses_are_kept(self) -> None:
        self.assertEqual(split_emotion("(웃음) 그래, 잘했다.", deceased=False), (None, "(웃음) 그래, 잘했다."))

    def test_no_tag(self) -> None:
        self.assertEqual(split_emotion("밥은 먹었냐", deceased=True), (None, "밥은 먹었냐"))


class InlineEmotionResponseTest(unittest.TestCase):
    """VOICE_EMOTION=inline 이면 같은 호출에서 감정을 받고, off 면 기존 그대로."""

    def _request(self, voice_id: str = "TalkToabc") -> PersonaResponseRequest:
        return PersonaResponseRequest.model_validate({
            "message": "할머니 나 취직했어",
            "persona": {"subjectId": "s1", "instructions": "너는 할머니다.", "deceased": True,
                        "voiceId": voice_id},
        })

    @patch("infra.external_api.openai_client.OpenAI")
    def test_inline_returns_emotion_and_clean_text(self, mock_openai: MagicMock) -> None:
        client = _openai_returning("[감정:happy] 아이고 장하다 우리 강아지!")
        mock_openai.return_value = client
        settings = Settings(openai_api_key=SecretStr("k"), voice_emotion="inline")
        result = generate_persona_response(self._request(), settings=settings)
        self.assertEqual(result.emotion, "happy")
        self.assertEqual(result.content, "아이고 장하다 우리 강아지!")
        self.assertEqual(client.chat.completions.create.call_count, 1)  # 따로 부르지 않는다
        system = [m["content"] for m in client.chat.completions.create.call_args.kwargs["messages"]
                  if m["role"] == "system"]
        self.assertTrue(any("[감정:이름]" in s for s in system))

    @patch("infra.external_api.openai_client.OpenAI")
    def test_off_keeps_previous_behaviour(self, mock_openai: MagicMock) -> None:
        client = _openai_returning("아이고 장하다!")
        mock_openai.return_value = client
        result = generate_persona_response(self._request(), settings=Settings(openai_api_key=SecretStr("k")))
        self.assertIsNone(result.emotion)
        system = [m["content"] for m in client.chat.completions.create.call_args.kwargs["messages"]
                  if m["role"] == "system"]
        self.assertFalse(any("[감정:이름]" in s for s in system))

    @patch("infra.external_api.openai_client.OpenAI")
    def test_elevenlabs_voice_gets_no_emotion_instruction(self, mock_openai: MagicMock) -> None:
        # v4 는 글을 보고 감정을 싣는다. 감정 지침을 넣지 않는다.
        client = _openai_returning("아이고 장하다!")
        mock_openai.return_value = client
        settings = Settings(openai_api_key=SecretStr("k"), voice_emotion="inline")
        result = generate_persona_response(self._request("aBcD1234efGH5678ijKL"), settings=settings)
        self.assertIsNone(result.emotion)
        system = [m["content"] for m in client.chat.completions.create.call_args.kwargs["messages"]
                  if m["role"] == "system"]
        self.assertFalse(any("[감정:이름]" in s for s in system))
