from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase


engine = create_async_engine("sqlite+aiosqlite:///db.sqlite3")
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session():
    """Get session."""
    async with async_session_factory() as session:
        yield session
    