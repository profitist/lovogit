from aiogram import BaseMiddleware
from aiogram.types import Message


class ChatAccessMiddleware(BaseMiddleware):
    def __init__(
        self, allowed_chat_ids: list[int], *, handler_data: dict | None = None
    ):
        self._allowed_chat_ids = frozenset(allowed_chat_ids)
        self._handler_data = handler_data or {}

    async def __call__(self, handler, event: Message, data):
        if event.chat.id not in self._allowed_chat_ids:
            await event.answer("Нет доступа", parse_mode=None)
            return None
        return await handler(event, {**data, **self._handler_data})
