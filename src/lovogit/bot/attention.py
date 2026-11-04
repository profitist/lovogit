import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from lovogit.app.services.attention import AttentionService, AttentionUnavailable
from lovogit.bot.attention_format import format_attention
from lovogit.bot.attention_middleware import AttentionMiddleware

logger = logging.getLogger(__name__)


async def attention(message: Message, attention_service: AttentionService):
    try:
        projects = await attention_service.get_overview()
    except AttentionUnavailable:
        logger.exception("Не удалось прочитать зависимости, требующие внимания")
        await message.answer(
            "Не удалось получить список зависимостей. Попробуйте позже", parse_mode=None
        )
        return
    for text in format_attention(projects):
        await message.answer(text, parse_mode=None)


def create_attention_router(allowed_chat_ids, sessions):
    router = Router(name="project-attention")
    router.message.middleware(AttentionMiddleware(allowed_chat_ids, sessions))
    router.message.register(attention, Command("attention"))
    return router
