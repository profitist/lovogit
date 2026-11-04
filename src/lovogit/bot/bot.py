import asyncio
from contextlib import AsyncExitStack

import httpx
from aiogram import Bot, Dispatcher

from lovogit.app.providers.github import GitHubProvider
from lovogit.app.services.registration import RepositoryRegistrationService
from lovogit.bot.add_repo import create_registration_router
from lovogit.bot.attention import create_attention_router
from lovogit.bot.commands import router
from lovogit.bot.status import create_status_router
from lovogit.cfg.github import GitHubSettings
from lovogit.db.engine import async_session_factory, engine


async def main() -> None:
    try:
        from lovogit.cfg import settings

        github_settings = GitHubSettings()
        async with AsyncExitStack() as resources:
            client = await resources.enter_async_context(httpx.AsyncClient())
            bot = Bot(token=settings.BOT_TOKEN.get_secret_value())
            resources.push_async_callback(bot.session.close)
            registration = RepositoryRegistrationService(
                GitHubProvider(client, github_settings), async_session_factory
            )
            dispatcher = Dispatcher()
            dispatcher.include_routers(
                router,
                create_status_router(settings.ALLOWED_CHAT_IDS, async_session_factory),
                create_attention_router(
                    settings.ALLOWED_CHAT_IDS, async_session_factory
                ),
                create_registration_router(settings.ALLOWED_CHAT_IDS, registration),
            )
            await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
