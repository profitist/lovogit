import asyncio

from aiogram import Bot, Dispatcher

from lovogit.bot.attention import create_attention_router
from lovogit.bot.commands import router
from lovogit.bot.status import create_status_router
from lovogit.db.engine import async_session_factory, engine


async def main() -> None:
    from lovogit.cfg import settings

    bot = Bot(token=settings.BOT_TOKEN.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher.include_routers(
        router,
        create_status_router(settings.ALLOWED_CHAT_IDS, async_session_factory),
        create_attention_router(settings.ALLOWED_CHAT_IDS, async_session_factory),
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
