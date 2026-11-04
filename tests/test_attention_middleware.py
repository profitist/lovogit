import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from aiogram.types import Message
from helpers import DatabaseCase
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from test_status_middleware import message

from lovogit.bot.attention_middleware import AttentionMiddleware


class AttentionMiddlewareTests(DatabaseCase):
    async def test_denied_and_empty_allowlist_do_not_open_session(self):
        sessions = AsyncMock()
        handler = AsyncMock()
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            for allowed in ([], [456]):
                await AttentionMiddleware(allowed, sessions)(handler, message(), {})
            self.assertEqual(answer.await_count, 2)
            answer.assert_awaited_with("Нет доступа", parse_mode=None)
        sessions.assert_not_called()
        handler.assert_not_awaited()

    async def test_concurrent_users_get_same_projects_and_separate_closed_sessions(
        self,
    ):
        opened = []
        closed = []
        results = []
        both_open = asyncio.Event()

        class TrackingSession(AsyncSession):
            async def close(self):
                closed.append(self)
                await super().close()

        @asynccontextmanager
        async def sessions():
            async with TrackingSession(self.engine) as session:
                opened.append(session)
                if len(opened) == 2:
                    both_open.set()
                yield session

        async def handler(event, data):
            await asyncio.wait_for(both_open.wait(), timeout=2)
            results.append(await data["attention_service"].get_overview())

        middleware = AttentionMiddleware([-100], sessions)
        await asyncio.gather(
            middleware(handler, message(1), {}), middleware(handler, message(2), {})
        )
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0].project_id, 1)
        self.assertEqual(len(opened), 2)
        self.assertIsNot(opened[0], opened[1])
        self.assertCountEqual(closed, opened)
        self.assertTrue(all(not session.in_transaction() for session in opened))

    async def test_session_closed_and_transaction_rolled_back_on_failure(self):
        closed = []
        rollbacks = []

        class TrackingSession(AsyncSession):
            async def close(self):
                closed.append(self)
                await super().close()

        @asynccontextmanager
        async def sessions():
            async with TrackingSession(self.engine) as session:
                yield session

        def record_rollback(connection):
            rollbacks.append(connection)

        async def handler(event, data):
            await data["attention_service"].get_overview()
            raise RuntimeError("Handler failed")

        event.listen(self.engine.sync_engine, "rollback", record_rollback)
        try:
            with self.assertRaisesRegex(RuntimeError, "Handler failed"):
                await AttentionMiddleware([-100], sessions)(handler, message(), {})
        finally:
            event.remove(self.engine.sync_engine, "rollback", record_rollback)
        self.assertEqual(len(closed), 1)
        self.assertEqual(len(rollbacks), 1)
        self.assertFalse(closed[0].in_transaction())
