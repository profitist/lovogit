import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from lovogit.app.providers.github.errors import RateLimited, SourceError
from lovogit.app.services.registration import (
    RegistrationConflict,
    RegistrationOutcome,
    RegistrationUnavailable,
    RepositoryRegistrationService,
)
from lovogit.bot.access_middleware import ChatAccessMiddleware
from lovogit.bot.status_format import format_time

logger = logging.getLogger(__name__)


class RegistrationMiddleware(ChatAccessMiddleware):
    def __init__(self, allowed_chat_ids, service: RepositoryRegistrationService):
        super().__init__(
            allowed_chat_ids, handler_data={"registration_service": service}
        )


async def add_repo(
    message: Message,
    command: CommandObject,
    registration_service: RepositoryRegistrationService,
):
    args = (command.args or "").split()
    if len(args) != 1:
        await message.answer(
            "Использование: /add_repo <github-url>\n"
            "Например: /add_repo https://github.com/owner/repo",
            parse_mode=None,
        )
        return
    try:
        result = await registration_service.add_repository(args[0])
    except SourceError as error:
        text = error.user_message
        if isinstance(error, RateLimited) and error.retry_at is not None:
            text += f". Повторите после {format_time(error.retry_at)}"
        await message.answer(text, parse_mode=None)
        return
    except RegistrationConflict:
        await message.answer(
            "Конфликт существующих записей репозитория. "
            "Обратитесь к администратору для исправления данных.",
            parse_mode=None,
        )
        return
    except RegistrationUnavailable as error:
        cause = error.__cause__
        logger.error(
            "Не удалось сохранить регистрацию репозитория: %s (причина: %s)",
            error,
            type(cause).__name__ if cause is not None else "неизвестна",
        )
        await message.answer(
            "Не удалось добавить репозиторий. Попробуйте позже", parse_mode=None
        )
        return
    labels = {
        RegistrationOutcome.ADDED: "Репозиторий добавлен",
        RegistrationOutcome.ALREADY_TRACKED: "Репозиторий уже отслеживается",
        RegistrationOutcome.RESTORED: "Отслеживание восстановлено",
    }
    await message.answer(
        f"{labels[result.outcome]}\n{result.project_name} (ID: {result.project_id})\n"
        f"{result.repo_url}\nАнализ не запущен",
        parse_mode=None,
    )


def create_registration_router(
    allowed_chat_ids, service: RepositoryRegistrationService
):
    router = Router(name="repository-registration")
    router.message.middleware(RegistrationMiddleware(allowed_chat_ids, service))
    router.message.register(add_repo, Command("add_repo"))
    return router
