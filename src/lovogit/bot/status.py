import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from lovogit.app.services.status import StatusService, StatusUnavailable
from lovogit.bot.status_format import format_overview
from lovogit.bot.status_middleware import StatusMiddleware

logger = logging.getLogger(__name__)


async def status(message: Message, status_service: StatusService):
    try:
        projects = await status_service.get_overview()
    except StatusUnavailable:
        logger.exception("Не удалось прочитать сводку проектов")
        await message.answer(
            "Не удалось получить статус. Попробуйте позже", parse_mode=None
        )
        return
    for text in format_overview(projects):
        await message.answer(text, parse_mode=None)


def create_status_router(allowed_chat_ids, sessions):
    router = Router(name="project-status")
    router.message.middleware(StatusMiddleware(allowed_chat_ids, sessions))
    router.message.register(status, Command("status"))
    return router
