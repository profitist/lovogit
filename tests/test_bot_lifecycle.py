import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Router
from pydantic import SecretStr

from lovogit import cfg
from lovogit.bot import bot as bot_module


class BotLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_error_prevents_polling_and_resources(self):
        with (
            patch.dict(cfg.__dict__, {"settings": SimpleNamespace()}),
            patch.object(
                bot_module, "GitHubSettings", side_effect=ValueError("missing token")
            ),
            patch.object(bot_module, "Dispatcher") as dispatcher,
            patch.object(bot_module.httpx, "AsyncClient") as client,
            patch.object(
                bot_module, "engine", SimpleNamespace(dispose=AsyncMock())
            ) as engine,
        ):
            with self.assertRaisesRegex(ValueError, "missing token"):
                await bot_module.main()
            dispatcher.assert_not_called()
            client.assert_not_called()
            engine.dispose.assert_awaited_once()

    async def test_resources_closed_after_polling_and_setup_failures(self):
        settings = SimpleNamespace(BOT_TOKEN=SecretStr("fake"), ALLOWED_CHAT_IDS=[-100])
        for failure in (None, "setup", "polling"):
            with self.subTest(failure=failure):
                client = AsyncMock()
                bot = MagicMock()
                bot.session.close = AsyncMock()
                dispatcher = MagicMock()
                dispatcher.start_polling = AsyncMock()
                if failure == "setup":
                    dispatcher.include_routers.side_effect = RuntimeError("setup")
                elif failure == "polling":
                    dispatcher.start_polling.side_effect = RuntimeError("polling")
                with (
                    patch.dict(cfg.__dict__, {"settings": settings}),
                    patch.object(bot_module, "GitHubSettings"),
                    patch.object(bot_module.httpx, "AsyncClient", return_value=client),
                    patch.object(bot_module, "Bot", return_value=bot),
                    patch.object(bot_module, "Dispatcher", return_value=dispatcher),
                    patch.object(bot_module, "router", Router()),
                    patch.object(
                        bot_module, "engine", SimpleNamespace(dispose=AsyncMock())
                    ) as engine,
                ):
                    if failure is None:
                        await bot_module.main()
                    else:
                        with self.assertRaisesRegex(RuntimeError, failure):
                            await bot_module.main()
                client.__aexit__.assert_awaited_once()
                bot.session.close.assert_awaited_once()
                engine.dispose.assert_awaited_once()

    def test_import_does_not_load_global_settings(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                'import lovogit.bot.bot; import sys; assert "lovogit.cfg.settings" not in sys.modules',
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
