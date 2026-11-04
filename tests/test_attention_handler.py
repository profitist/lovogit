from dataclasses import replace
from unittest.mock import AsyncMock, call, patch

from aiogram import Bot, Dispatcher
from aiogram.types import Message, Update
from helpers import DatabaseCase
from test_attention_format import project
from test_status_middleware import message

from lovogit.app.services.attention import AttentionService, AttentionUnavailable
from lovogit.app.services.monitoring import DependencyResultDTO, MonitoringStateService
from lovogit.bot.attention import attention, create_attention_router
from lovogit.bot.attention_format import format_attention
from lovogit.bot.commands import router as commands_router
from lovogit.bot.status import create_status_router


class AttentionHandlerTests(DatabaseCase):
    async def test_reads_only_service_and_sends_all_plain_text_parts(self):
        service = AsyncMock(spec=AttentionService)
        projects = [replace(project(), project_name="<backend>_*😀" * 1000)]
        service.get_overview.return_value = projects
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await attention(message(), service)
        self.assertEqual(service.mock_calls, [call.get_overview()])
        self.assertEqual(
            [c.args[0] for c in answer.await_args_list], format_attention(projects)
        )
        self.assertGreater(answer.await_count, 1)
        self.assertTrue(
            all(c.kwargs == {"parse_mode": None} for c in answer.await_args_list)
        )

    async def test_read_error_logged_and_neutral_reply(self):
        service = AsyncMock(spec=AttentionService)
        service.get_overview.side_effect = AttentionUnavailable("SQL private detail")
        with (
            patch.object(Message, "answer", new_callable=AsyncMock) as answer,
            self.assertLogs("lovogit.bot.attention", level="ERROR") as logs,
        ):
            await attention(message(), service)
        answer.assert_awaited_once_with(
            "Не удалось получить список зависимостей. Попробуйте позже", parse_mode=None
        )
        self.assertIn("SQL private detail", logs.output[0])

    async def test_send_failure_propagates_without_database_reply(self):
        service = AsyncMock(spec=AttentionService)
        service.get_overview.return_value = []
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            answer.side_effect = RuntimeError("Telegram offline")
            with self.assertRaisesRegex(RuntimeError, "Telegram offline"):
                await attention(message(), service)
        self.assertEqual(answer.await_count, 1)

    async def test_full_dispatcher_routing_access_and_status_regression(self):
        run = await MonitoringStateService(self.sessions).start_run(1)
        await MonitoringStateService(self.sessions).succeed_run(
            run, [DependencyResultDTO("requests", "1", "2", "review")]
        )
        bot = Bot("123456:fake-token")
        self.addAsyncCleanup(bot.session.close)
        dispatcher = Dispatcher()
        dispatcher.include_routers(
            commands_router,
            create_status_router([-100], self.sessions),
            create_attention_router([-100], self.sessions),
        )
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=1,
                    message=message().model_copy(update={"text": "/attention"}),
                ),
            )
            self.assertIn(
                "requests: 1 → 2 — Требует проверки", answer.await_args.args[0]
            )
            self.assertNotIn("заглушка", answer.await_args.args[0])
            answer.reset_mock()
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=2,
                    message=message(chat_id=-200).model_copy(
                        update={"text": "/attention"}
                    ),
                ),
            )
            answer.assert_awaited_once_with("Нет доступа", parse_mode=None)
            answer.reset_mock()
            await dispatcher.feed_update(bot, Update(update_id=3, message=message()))
            self.assertIn("Требует проверки: 1", answer.await_args.args[0])
