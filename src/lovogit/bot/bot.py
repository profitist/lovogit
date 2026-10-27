import asyncio

from aiogram import Bot, Dispatcher

from lovogit.bot.commands import router
from lovogit.cfg import settings


async def main() -> None:
    bot = Bot(token=settings.BOT_TOKEN.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher.include_routers(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
