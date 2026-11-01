import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from pydantic_settings import SettingsError

from lovogit.cfg.access import BotAccessSettings


class AccessSettingsTests(unittest.TestCase):
    def test_default_denies_all(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(BotAccessSettings(_env_file=None).ALLOWED_CHAT_IDS, [])

    def test_json_negative_group_id(self):
        with patch.dict(os.environ, {"ALLOWED_CHAT_IDS": "[-100123, 456]"}):
            self.assertEqual(
                BotAccessSettings(_env_file=None).ALLOWED_CHAT_IDS, [-100123, 456]
            )

    def test_invalid_values(self):
        for value in ("-100123", "invalid", '["123"]', "[true]", "[1.2]"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"ALLOWED_CHAT_IDS": value}),
                self.assertRaises((ValidationError, SettingsError)),
            ):
                BotAccessSettings(_env_file=None)
