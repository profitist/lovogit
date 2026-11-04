from functools import partial

from aiogram import BaseMiddleware
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lovogit.bot.access_middleware import ChatAccessMiddleware


class SessionServiceMiddleware(BaseMiddleware):
    def __init__(
        self, allowed_chat_ids: list[int], sessions: async_sessionmaker[AsyncSession]
    ):
        self._access = ChatAccessMiddleware(allowed_chat_ids)
        self._sessions = sessions

    async def __call__(self, handler, event: Message, data):
        return await self._access(partial(self._with_service, handler), event, data)

    async def _with_service(self, handler, event: Message, data):
        async with self._sessions() as session:
            return await handler(
                event, {**data, self._service_key: self._service_type(session)}
            )
