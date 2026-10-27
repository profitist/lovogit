import dataclasses

from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from lovogit.bot.message import message_builder
from lovogit.bot.message import BaseMessageStructure


router = Router()


@router.message(CommandStart())
async def start(message: Message):
    class StartMessageStructure(BaseMessageStructure):
        title = "LovoGit"
        content = (
            "Слежу за обновлениями зависимостей проекта и объясняю, "
            "что новая версия значит для вашего кода: что изменилось, "
            "какие места задевает и насколько рискованно обновляться.\n\n"
            "Решение об обновлении остаётся за вами — код и зависимости я не меняю."
        )
        footer = "/help — список команд"

    await message.answer(text=message_builder.build(StartMessageStructure()))


@router.message(Command("add_repo"))
async def add_repo(message: Message):
    class AddRepoMessageStructure(BaseMessageStructure):
        title = "Подключение проекта"
        content = (
            "Подключение проекта через бота пока не поддерживается: "
            "проект задаётся конфигурацией при развёртывании LovoGit."
        )
        footer = "/help — список команд"

    await message.answer(text=message_builder.build(AddRepoMessageStructure()))


@router.message(Command("status"))
async def status(message: Message):
    class StatusMessageStructure(BaseMessageStructure):
        title = "Статус проекта"
        content = (
            "Здесь будет время и результат последней проверки "
            "и число зависимостей в каждом статусе.\n\n"
            "Пока заглушка: проверки ещё не реализованы."
        )
        footer = "/help — список команд"

    await message.answer(text=message_builder.build(StatusMessageStructure()))


@router.message(Command("attention"))
async def attention(message: Message):
    class AttentionMessageStructure(BaseMessageStructure):
        title = "Требует внимания"
        content = (
            "Здесь будут зависимости со статусами «Вероятна несовместимость», "
            "«Требует проверки» и «Анализ не удался».\n\n"
            "Пока заглушка: проверки ещё не реализованы."
        )
        footer = "/help — список команд"

    await message.answer(text=message_builder.build(AttentionMessageStructure()))


@router.message(Command("dep"))
async def dep(message: Message):
    class DepMessageStructure(BaseMessageStructure):
        title = "Отчёт по зависимости"
        content = (
            "Использование: /dep <имя>, например /dep requests.\n\n"
            "Здесь будут версии, статус, причина оценки, число затронутых мест "
            "и ссылка на официальный источник.\n\n"
            "Пока заглушка: проверки ещё не реализованы."
        )
        footer = "/help — список команд"

    await message.answer(text=message_builder.build(DepMessageStructure()))


@router.message(Command("check"))
async def check(message: Message):
    class CheckMessageStructure(BaseMessageStructure):
        title = "Проверка"
        content = (
            "Здесь будет запуск проверки вне расписания: "
            "итог придёт сообщением, новые находки — уведомлениями.\n\n"
            "Пока заглушка: проверка не запущена."
        )
        footer = "/help — список команд"

    await message.answer(text=message_builder.build(CheckMessageStructure()))


@router.message(Command("help"))
async def help_(message: Message):
    class HelpMessageStructure(BaseMessageStructure):
        title = "Команды"
        content = (
            "/status — сводка по проекту\n"
            "/attention — что требует внимания\n"
            "/dep <имя> — отчёт по зависимости\n"
            "/check — запустить проверку вне расписания\n"
            "/help — список команд"
        )
        footer = "Подробные отчёты — в Mini App"

    await message.answer(text=message_builder.build(HelpMessageStructure()))
