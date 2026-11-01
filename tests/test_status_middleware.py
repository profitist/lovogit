import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, Message, User
from helpers import DatabaseCase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lovogit.bot.status_middleware import StatusMiddleware


def message(user_id=1, chat_id=-100):
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="group"),
        from_user=User(id=user_id, is_bot=False, first_name="User"),
        text="/status",
    )


class StatusMiddlewareTests(DatabaseCase):
    async def test_denied_and_empty_access_do_not_open_session(self):
        sessions = AsyncMock()
        handler = AsyncMock()
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            for allowed in ([], [456]):
                await StatusMiddleware(allowed, sessions)(handler, message(), {})
            self.assertEqual(answer.await_count, 2)
            answer.assert_awaited_with("Нет доступа", parse_mode=None)
        sessions.assert_not_called()
        handler.assert_not_awaited()

    async def test_equal_access_and_isolated_sessions_for_concurrent_requests(self):
        seen = []
        both_open = asyncio.Event()

        async def handler(event, data):
            seen.append(data["status_service"].session)
            if len(seen) == 2:
                both_open.set()
            await asyncio.wait_for(both_open.wait(), timeout=2)
            self.assertEqual(
                (await data["status_service"].get_overview())[0].project_id, 1
            )

        middleware = StatusMiddleware([-100], self.sessions)
        await asyncio.gather(
            middleware(handler, message(1), {}), middleware(handler, message(2), {})
        )
        self.assertEqual(len(seen), 2)
        self.assertIsNot(seen[0], seen[1])
        self.assertTrue(all(not session.in_transaction() for session in seen))

    async def test_session_closed_and_transaction_rolled_back_on_exception(self):
        captured = []
        closed = []

        class TrackingSession(AsyncSession):
            async def close(self):
                closed.append(self)
                await super().close()

        @asynccontextmanager
        async def sessions():
            async with TrackingSession(self.engine) as session:
                captured.append(session)
                yield session

        async def handler(event, data):
            await data["status_service"].session.execute(
                text(
                    "INSERT INTO repos (id,name,repo_url) VALUES (2,'uncommitted','url')"
                )
            )
            raise RuntimeError("Имитированный сбой")

        with self.assertRaises(RuntimeError):
            await StatusMiddleware([-100], sessions)(handler, message(), {})
        self.assertEqual(closed, captured)
        self.assertFalse(captured[0].in_transaction())
        async with self.sessions() as session:
            self.assertEqual(
                await session.scalar(text("SELECT count(*) FROM repos")), 1
            )
