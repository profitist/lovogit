from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.types import Message, Update
from helpers import DatabaseCase
from test_status_format import project
from test_status_middleware import message

from lovogit.app.services.status import StatusService, StatusUnavailable
from lovogit.bot.status import create_status_router, status


class HandlerTests(DatabaseCase):
    async def test_success_calls_only_read_service_and_sends_plain_text_in_order(self):
        service = AsyncMock(spec=StatusService)
        service.get_overview.return_value = [project(name="<backend>_😀" * 1000)]
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await status(message(), service)
        service.get_overview.assert_awaited_once_with()
        self.assertGreater(answer.await_count, 1)
        parts = []
        for call in answer.await_args_list:
            self.assertEqual(call.kwargs, {"parse_mode": None})
            parts.append(call.args[0])
        self.assertIn("<backend>_😀" * 1000, "".join(parts))

    async def test_storage_error_logged_and_neutral_reply(self):
        service = AsyncMock()
        service.get_overview.side_effect = StatusUnavailable("SQL private data")
        with (
            patch.object(Message, "answer", new_callable=AsyncMock) as answer,
            self.assertLogs("lovogit.bot.status", level="ERROR") as logs,
        ):
            await status(message(), service)
        answer.assert_awaited_once_with(
            "Не удалось получить статус. Попробуйте позже", parse_mode=None
        )
        self.assertIn("StatusUnavailable", logs.output[0])
        self.assertIn("SQL private data", logs.output[0])

    async def test_telegram_send_error_not_reported_as_database_error(self):
        service = AsyncMock()
        service.get_overview.return_value = []
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            answer.side_effect = RuntimeError("Telegram offline")
            with self.assertRaisesRegex(RuntimeError, "Telegram offline"):
                await status(message(), service)
        self.assertEqual(answer.await_count, 1)

    async def test_dispatcher_routes_status_and_denies_other_chat(self):
        bot = Bot("123456:fake-token")
        self.addAsyncCleanup(bot.session.close)
        dispatcher = Dispatcher()
        dispatcher.include_router(create_status_router([-100], self.sessions))
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await dispatcher.feed_update(bot, Update(update_id=1, message=message()))
            self.assertIn("backend", answer.await_args.args[0])
            answer.reset_mock()
            await dispatcher.feed_update(
                bot, Update(update_id=2, message=message(chat_id=-200))
            )
            answer.assert_awaited_once_with("Нет доступа", parse_mode=None)
