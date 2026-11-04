from aiogram import BaseMiddleware
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lovogit.app.services.attention import AttentionService


class AttentionMiddleware(BaseMiddleware):
    """Доступ к /attention и отдельная сессия для разрешённого запроса."""

    def __init__(
        self, allowed_chat_ids: list[int], sessions: async_sessionmaker[AsyncSession]
    ):
        self.allowed_chat_ids = frozenset(allowed_chat_ids)
        self.sessions = sessions

    async def __call__(self, handler, event: Message, data):
        if event.chat.id not in self.allowed_chat_ids:
            await event.answer("Нет доступа", parse_mode=None)
            return None
        async with self.sessions() as session:
            return await handler(
                event, {**data, "attention_service": AttentionService(session)}
            )
