from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from lovogit.cfg.database import DatabaseSettings


class Base(DeclarativeBase):
    pass


def make_engine(url: str, **options):
    """Создать движок, включив проверку внешних ключей SQLite."""
    result = create_async_engine(url, **options)
    if result.dialect.name == "sqlite":

        @event.listens_for(result.sync_engine, "connect")
        def enable_foreign_keys(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return result


engine = make_engine(DatabaseSettings().DATABASE_URL)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    """Открыть отдельную сессию на операцию."""
    async with async_session_factory() as session:
        yield session
