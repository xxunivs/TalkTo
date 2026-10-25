import unittest
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from infra.external_api.tts.fish import FISH_INLINE_CUES
from infra.schema.persona import PersonaResponseRequest
from infra.setting import Settings
from pipeline.s3_synthesis.c_build_system_prompt import generate_persona_response
from pipeline.s4_voice.b_emotion import (
    FISH_CUE_INSTRUCTION,
    FISH_EMOTIONS,
    FISH_INLINE_INSTRUCTION,
    split_emotion,
    split_voice,
    voice_instruction,
    voice_mode,
)


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
        # [sad] 처럼 감정 이름만 붙은 꼴도 떼야 소리로 읽히지 않는다
        for raw, want in (("[sad] 도윤아, 보고 싶다.", "sad"), ("(감정: 기쁨) 잘됐다!", "happy"),
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

    @patch("infra.external_api.openai_client.OpenAI")
    def test_fish_voice_gets_no_emotion_instruction(self, mock_openai: MagicMock) -> None:
        # drama-3 는 감정 표시 없이도 감정을 싣는다. [ ] 표시는 오히려 연기 지시로 읽힌다.
        client = _openai_returning("아이고 장하다!")
        mock_openai.return_value = client
        settings = Settings(openai_api_key=SecretStr("k"), voice_emotion="inline")
        result = generate_persona_response(self._request("fish_0123abcd"), settings=settings)
        self.assertIsNone(result.emotion)
        system = [m["content"] for m in client.chat.completions.create.call_args.kwargs["messages"]
                  if m["role"] == "system"]
        self.assertFalse(any("[감정:이름]" in s for s in system))


def _fish_request(voice_id: str | None = "fish_0123abcd", deceased: bool | None = True,
                  fixed_prompt: bool = False) -> PersonaResponseRequest:
    return PersonaResponseRequest.model_validate({
        "message": "할머니 나 취직했어", "memoryTotal": 300,  # 기억 많은 페르소나(기억 적음 절 없음)
        "persona": {"subjectId": "s1", "instructions": "너는 할머니다.", "deceased": deceased,
                    "voiceId": voice_id, "fixedPrompt": fixed_prompt},
    })


def _system(client: MagicMock) -> list[str]:
    return [m["content"] for m in client.chat.completions.create.call_args.kwargs["messages"]
            if m["role"] == "system"]


class FishCueEmotionTest(unittest.TestCase):
    """FISH_EMOTION=cue: 감정 이름 하나(새 이름 warm·worried 포함), 고인 안전선, MiniMax 와 다른 이름표."""

    def test_new_names_and_korean_shapes(self) -> None:
        self.assertEqual(FISH_EMOTIONS, ("neutral", "calm", "warm", "happy", "sad", "surprised", "worried"))
        for raw, want in (("[감정:warm] 밥은 먹었니?", "warm"), ("[감정:worried] 늦게 다니지 마.", "worried"),
                          ("(감정: 걱정) 아프면 안 된다.", "worried"), ("[감정:다정] 그래, 우리 강아지.", "warm"),
                          ("[감정:neutral] 응.", "neutral"), ("[감정:angry] 뭐라고?", None)):
            voice = split_voice(raw, "cue", deceased=False)
            self.assertEqual(voice.emotion, want, raw)
            self.assertFalse(voice.text.startswith(("[", "(")), voice.text)
            self.assertIsNone(voice.speech_text())

    def test_deceased_cap(self) -> None:
        self.assertEqual(split_voice("[감정:sad] 그래…", "cue", deceased=True).emotion, "calm")
        self.assertEqual(split_voice("[감정:sad] 그래…", "cue", deceased=None).emotion, "calm")  # 모르면 고인
        self.assertEqual(split_voice("[감정:sad] 그래…", "cue", deceased=False).emotion, "sad")
        self.assertEqual(split_voice("[감정:worried] 그래…", "cue", deceased=True).emotion, "worried")

    def test_minimax_names_unchanged(self) -> None:
        # MiniMax 지침(VOICE_EMOTION)은 예전 다섯 이름 그대로. warm·worried 가 와도 None
        self.assertIsNone(split_voice("[감정:warm] 그래.", "minimax", deceased=False).emotion)
        self.assertEqual(split_voice("[감정:happy] 그래.", "minimax", deceased=False).emotion, "happy")

    def test_stray_fish_cues_do_not_reach_the_screen(self) -> None:
        voice = split_voice("[감정:warm] 그래, [다정하게] 잘 왔다.", "cue", deceased=False)
        self.assertEqual((voice.emotion, voice.text), ("warm", "그래, 잘 왔다."))
        # 목록 밖 표시(울음 포함)도 화면·가드·기록에 남기지 않는다(inline 과 같다)
        voice = split_voice("[감정:sad] 아이고 [울면서] 보고 싶다 [미소].", "cue", deceased=True)
        self.assertEqual((voice.emotion, voice.text, voice.speech_text()), ("calm", "아이고 보고 싶다.", None))

    @patch("infra.external_api.openai_client.OpenAI")
    def test_plain_path_one_call(self, mock_openai: MagicMock) -> None:
        client = _openai_returning("[감정:warm] 아이고 장하다 우리 강아지!")
        mock_openai.return_value = client
        settings = Settings(openai_api_key=SecretStr("k"), fish_emotion="cue")
        result = generate_persona_response(_fish_request(), settings=settings)
        self.assertEqual((result.emotion, result.content, result.speech_text),
                         ("warm", "아이고 장하다 우리 강아지!", None))
        self.assertEqual(client.chat.completions.create.call_count, 1)  # 따로 부르지 않는다
        system = _system(client)
        self.assertEqual(system[0], "너는 할머니다.")
        self.assertEqual(system[1], FISH_CUE_INSTRUCTION)  # 지시문 바로 뒤

    @patch("infra.external_api.openai_client.OpenAI")
    def test_cue_is_only_for_fish_voices(self, mock_openai: MagicMock) -> None:
        client = _openai_returning("[감정:warm] 아이고!")
        mock_openai.return_value = client
        settings = Settings(openai_api_key=SecretStr("k"), fish_emotion="cue")
        for voice_id in ("aBcD1234efGH5678ijKL", "TalkToabc", None):  # ElevenLabs·MiniMax(VOICE_EMOTION off)·없음
            result = generate_persona_response(_fish_request(voice_id), settings=settings)
            self.assertIsNone(result.emotion, voice_id)
            self.assertEqual(result.content, "[감정:warm] 아이고!", voice_id)  # 지금과 같다(건드리지 않음)
            self.assertEqual(len(_system(client)), 1, voice_id)


class FishInlineCueTest(unittest.TestCase):
    """FISH_EMOTION=inline: 정해진 연기 표시만 남기고(최대 2개, 맨 끝 금지), 화면 글에는 표시가 없다."""

    def test_whitelist_and_limit(self) -> None:
        voice = split_voice("[웃으며] 그래, [미소] 잘 왔다. [작게] 근데 [한숨 쉬며] 밥은?", "inline", deceased=True)
        self.assertEqual(voice.text, "그래, 잘 왔다. 근데 밥은?")
        self.assertEqual(voice.speech_text(), "[웃으며] 그래, 잘 왔다. [작게] 근데 밥은?")  # 셋째 표시는 버린다
        self.assertIsNone(voice.emotion)

    def test_unknown_crying_and_trailing_cues_are_dropped(self) -> None:
        for raw, text in (("[울며] 보고 싶다.", "보고 싶다."), ("[흐느끼며] 그래.", "그래."),
                          ("그래, 잘 자. [웃으며]", "그래, 잘 자."), ("[감정:sad] 그래.", "그래."),
                          ("그래 [미소].", "그래."), ("그래 [웃으며] .", "그래.")):
            voice = split_voice(raw, "inline", deceased=True)
            self.assertEqual(voice.text, text, raw)
            self.assertIsNone(voice.speech_text(), raw)

    def test_cue_spacing_is_normalized_and_mid_sentence_kept(self) -> None:
        voice = split_voice("그랬구나, [ 작게  웃으며 ] 우리 강아지.", "inline", deceased=False)
        self.assertEqual(voice.text, "그랬구나, 우리 강아지.")
        self.assertEqual(voice.speech_text(), "그랬구나, [작게 웃으며] 우리 강아지.")

    def test_cue_glued_between_words_does_not_join_them(self) -> None:
        # 표시가 앞말에 빈칸 없이 붙어 와도 화면 글의 두 낱말이 붙지 않는다(글자를 바꾸지 않는다)
        for raw, text, speech in (
            ("아이고[한숨 쉬며] 그랬구나.", "아이고 그랬구나.", "아이고 [한숨 쉬며] 그랬구나."),
            ("아이고[한숨 쉬며]그랬구나.", "아이고 그랬구나.", "아이고 [한숨 쉬며] 그랬구나."),
            ("그래.[웃으며]왔니?", "그래. 왔니?", "그래. [웃으며] 왔니?"),
            ("그래[미소][작게] 왔니?", "그래 왔니?", "그래 [작게] 왔니?"),
            ("아이고 [한숨 쉬며]그랬구나.", "아이고 그랬구나.", "아이고 [한숨 쉬며] 그랬구나."),
            ("그래.\n[웃으며] 반갑다.", "그래.\n반갑다.", "그래.\n[웃으며] 반갑다."),  # 줄바꿈 뒤에는 빈칸을 넣지 않는다
            ("그래 [미소].", "그래.", None),
        ):
            voice = split_voice(raw, "inline", deceased=True)
            self.assertEqual((voice.text, voice.speech_text()), (text, speech), raw)

    def test_place_cues_follows_the_guard(self) -> None:
        # 가드가 문장을 지우면 그 문장의 표시도 빠지고, 남은 문장의 표시는 같은 자리에 남는다
        voice = split_voice("[웃으며] 그래, 잘 왔다. [걱정스럽게] 밥은 챙겨 먹어라.", "inline", deceased=True)
        self.assertEqual(voice.speech_text("그래, 잘 왔다."), "[웃으며] 그래, 잘 왔다.")
        self.assertEqual(voice.speech_text("밥은 챙겨 먹어라."), "[걱정스럽게] 밥은 챙겨 먹어라.")
        self.assertIsNone(voice.speech_text("글쎄, 그건 기억이 잘 안 나네."))

    @patch("infra.external_api.openai_client.OpenAI")
    def test_plain_path_returns_clean_content_and_speech_text(self, mock_openai: MagicMock) -> None:
        client = _openai_returning("[반갑게] 아이고, 장하다! [작게 웃으며] 우리 강아지 고생했다.")
        mock_openai.return_value = client
        settings = Settings(openai_api_key=SecretStr("k"), fish_emotion="inline")
        result = generate_persona_response(_fish_request(), settings=settings)
        self.assertEqual(result.content, "아이고, 장하다! 우리 강아지 고생했다.")
        self.assertEqual(result.speech_text, "[반갑게] 아이고, 장하다! [작게 웃으며] 우리 강아지 고생했다.")
        self.assertIsNone(result.emotion)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        system = _system(client)
        self.assertEqual(system[1], FISH_INLINE_INSTRUCTION)
        self.assertIn(" ".join(FISH_INLINE_CUES), system[1])
        self.assertFalse(any("울" in cue or "흐느" in cue for cue in FISH_INLINE_CUES))  # 고인 안전선

    @patch("infra.external_api.openai_client.OpenAI")
    def test_fixed_prompt_persona_gets_it_too(self, mock_openai: MagicMock) -> None:
        # 가져온 지침(fixedPrompt)도 기존 경로로 같은 지침을 받는다(말투 덧붙임만 빠진다)
        client = _openai_returning("[웃으며] 왔니.")
        mock_openai.return_value = client
        settings = Settings(openai_api_key=SecretStr("k"), fish_emotion="inline")
        result = generate_persona_response(_fish_request(fixed_prompt=True), settings=settings)
        self.assertEqual((result.content, result.speech_text), ("왔니.", "[웃으며] 왔니."))
        self.assertEqual(_system(client)[1], FISH_INLINE_INSTRUCTION)

    @patch("infra.external_api.openai_client.OpenAI")
    def test_off_has_no_speech_text(self, mock_openai: MagicMock) -> None:
        client = _openai_returning("[웃으며] 왔니.")
        mock_openai.return_value = client
        result = generate_persona_response(_fish_request(), settings=Settings(openai_api_key=SecretStr("k")))
        self.assertIsNone(result.speech_text)
        self.assertNotIn("speechText", result.model_dump(by_alias=True))
        self.assertEqual(result.content, "[웃으며] 왔니.")  # 지금과 같다(읽을 때 Fish 가 표시를 지운다)
        self.assertEqual(len(_system(client)), 1)

    def test_instructions_keep_polite_speech(self) -> None:
        # 지침에 반말 예시 문장을 넣지 않는다(존댓말로 대하는 상대에게 말투가 흔들리지 않게)
        for text in (FISH_CUE_INSTRUCTION, FISH_INLINE_INSTRUCTION):
            self.assertIn("높임말", text)
            for casual in ("했다.", "먹었냐", "그래,", "왔니"):
                self.assertNotIn(casual, text)


class VoiceModeTest(unittest.TestCase):
    def test_mode_by_voice_and_settings(self) -> None:
        cue = Settings(fish_emotion="cue", voice_emotion="inline")
        self.assertEqual(voice_mode(cue, "fish_abc"), "cue")
        self.assertEqual(voice_mode(cue, "TalkTo1"), "minimax")  # MiniMax 는 VOICE_EMOTION 그대로
        self.assertEqual(voice_mode(cue, "aBcD1234efGH5678ijKL"), "off")  # ElevenLabs 는 늘 off
        self.assertEqual(voice_mode(Settings(), "fish_abc"), "off")
        self.assertEqual(voice_mode(Settings(fish_emotion="inline"), "fish_abc"), "inline")
        self.assertIsNone(voice_instruction("off"))
