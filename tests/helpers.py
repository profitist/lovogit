import tempfile
import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from lovogit.db import RepoModel
from lovogit.db.engine import Base, make_engine


class DatabaseCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.url = f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'test.sqlite3'}"
        self.engine = make_engine(self.url)
        self.addAsyncCleanup(self.engine.dispose)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions.begin() as session:
            session.add(RepoModel(id=1, name="backend", repo_url="url"))
