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
