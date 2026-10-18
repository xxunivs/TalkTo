import os
import unittest
from unittest.mock import patch

from infra.setting import load_settings


class ConfigTest(unittest.TestCase):
    def test_defaults_load_without_provider_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(env_file=None)

        self.assertEqual(settings.app_env, "local")
        self.assertEqual(settings.openai_chat_model, "gpt-6-luna")
        self.assertEqual(settings.openai_analysis_model, "gpt-6-luna")
        self.assertEqual(settings.openai_stt_model, "whisper-1")
        self.assertEqual(
            settings.openai_embedding_model,
            "text-embedding-3-small",
        )
        self.assertEqual(settings.openai_embedding_dimensions, 1536)
        self.assertIsNone(settings.openai_api_key)
        self.assertIsNone(settings.fish_audio_api_key)
        self.assertEqual(settings.tts_provider, "fish")
        self.assertEqual(settings.elevenlabs_stt_model, "scribe_v2")
        # 페르소나 프롬프트: 4층 + 주고받기는 메시지로. 중간계층·사전 판정·가드는 꺼 둔다
        self.assertEqual(settings.persona_prompt_builder, "layered")
        self.assertEqual(settings.orchestrator_rules, "layered")
        self.assertEqual(settings.orchestrator_shots, "messages")
        self.assertFalse(settings.persona_orchestrator)
        self.assertEqual(settings.orchestrator_assembler, "legacy")
        self.assertEqual(settings.orchestrator_precheck, "noop")
        self.assertEqual(settings.orchestrator_guard, "pass")
        # 세션 14·17·18: 채점 모델, 기억 수 상한(경고만), 질문 다시 쓰기(꺼짐), 말투 카드 문턱, 말투 덧붙임(켜짐)
        self.assertEqual(settings.openai_judge_model, "gpt-6-luna")
        self.assertEqual(settings.memory_full_max, 400)
        self.assertEqual(settings.query_rewrite, "off")
        self.assertEqual(settings.style_card_min_utts, 30)
        self.assertEqual(settings.style_late_note, "on")

    def test_precheck_off_in_built_orchestrator(self) -> None:
        # 설정값만이 아니라 실제로 조립되는 중간계층도 사전 판정 없음·가드 통과여야 한다
        from pydantic import SecretStr

        from infra.setting import Settings
        from orchestrator.service import build_default

        parts = build_default(Settings(openai_api_key=SecretStr("k"))).describe()
        self.assertEqual(parts["precheck"], "noop_v0")
        self.assertEqual(parts["guard"], "pass_v0")

    def test_legacy_prompt_flags_still_selectable(self) -> None:
        env = {"PERSONA_PROMPT_BUILDER": "legacy", "ORCHESTRATOR_RULES": "legacy", "ORCHESTRATOR_SHOTS": "system",
               "STYLE_LATE_NOTE": "off", "QUERY_REWRITE": "on", "MEMORY_FULL_MAX": "50", "STYLE_CARD_MIN_UTTS": "8"}
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings(env_file=None)
        self.assertEqual(settings.persona_prompt_builder, "legacy")
        self.assertEqual(settings.orchestrator_rules, "legacy")
        self.assertEqual(settings.orchestrator_shots, "system")
        self.assertEqual(settings.style_late_note, "off")
        self.assertEqual(settings.query_rewrite, "on")
        self.assertEqual(settings.memory_full_max, 50)
        self.assertEqual(settings.style_card_min_utts, 8)

    def test_voice_keys_and_models_from_environment(self) -> None:
        env = {"FISH_AUDIO_API_KEY": "fish-key", "TTS_PROVIDER": "", "ELEVENLABS_STT_MODEL": ""}
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings(env_file=None)
        self.assertEqual(settings.fish_audio_api_key.get_secret_value(), "fish-key")
        self.assertEqual(settings.tts_provider, "fish")  # 비워 두면 fish
        self.assertEqual(settings.elevenlabs_stt_model, "scribe_v2")
        with patch.dict(os.environ, {"FISH_AUDIO_API_KEY": " "}, clear=True):
            self.assertIsNone(load_settings(env_file=None).fish_audio_api_key)

    def test_deploy_can_find_the_fish_key(self) -> None:
        # BE deploy.py 는 infra/setting.py 에 글자 그대로 적힌 os.getenv 이름만 운영 비밀값으로 옮긴다
        import re
        from pathlib import Path

        import infra.setting

        text = Path(infra.setting.__file__).read_text(encoding="utf-8")
        names = set(re.findall(r'os\.(?:getenv|environ\.get)\(\s*"([A-Z0-9_]+)"', text))
        self.assertTrue({"FISH_AUDIO_API_KEY", "ELEVENLABS_API_KEY", "MINIMAX_API_KEY"} <= names)
        # 새 기능 플래그도 같은 방식으로 운영에 올라가야 .env 로 켜고 끌 수 있다
        self.assertTrue({"MEMORY_FULL_MAX", "QUERY_REWRITE", "STYLE_CARD_MIN_UTTS", "STYLE_LATE_NOTE"} <= names)

    def test_environment_overrides_are_validated(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "LOG_LEVEL": "warning",
                "OPENAI_CHAT_MODEL": "test-chat-model",
                "OPENAI_EMBEDDING_DIMENSIONS": "256",
            },
            clear=True,
        ):
            settings = load_settings(env_file=None)

        self.assertEqual(settings.app_env, "test")
        self.assertEqual(settings.log_level, "WARNING")
        self.assertEqual(settings.openai_chat_model, "test-chat-model")
        self.assertEqual(settings.openai_embedding_dimensions, 256)


if __name__ == "__main__":
    unittest.main()
