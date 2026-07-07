import os
import unittest
from unittest.mock import patch

from app.core.config import load_settings


class ConfigTest(unittest.TestCase):
    def test_defaults_load_without_provider_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(env_file=None)

        self.assertEqual(settings.app_env, "local")
        self.assertEqual(settings.openai_chat_model, "gpt-4.1-mini")
        self.assertEqual(settings.openai_analysis_model, "gpt-5.4-mini")
        self.assertEqual(settings.openai_stt_model, "whisper-1")
        self.assertEqual(
            settings.openai_embedding_model,
            "text-embedding-3-small",
        )
        self.assertEqual(settings.openai_embedding_dimensions, 1536)
        self.assertIsNone(settings.openai_api_key)

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
