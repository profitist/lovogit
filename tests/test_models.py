import unittest
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from lovogit.db import (
    DependencyResult,
    DependencyStatus,
    MonitoringRun,
    RepoModel,
    RunState,
)
from lovogit.db.engine import Base, make_engine


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = make_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(self.engine.dispose)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions.begin() as session:
            session.add(RepoModel(id=1, name="a", repo_url="url"))
        async with self.sessions.begin() as session:
            session.add(
                MonitoringRun(
                    id=1,
                    repo_id=1,
                    started_at=datetime.now(UTC),
                    state=RunState.RUNNING,
                )
            )

    async def test_all_statuses_persist(self):
        async with self.sessions.begin() as session:
            session.add_all(
                [
                    DependencyResult(
                        run_id=1,
                        name=status,
                        current_version="1",
                        latest_version=None,
                        status=status,
                    )
                    for status in DependencyStatus
                ]
            )
        async with self.sessions() as session:
            results = (await session.scalars(select(DependencyResult))).all()
            self.assertEqual({r.status for r in results}, set(DependencyStatus))
            self.assertTrue(all(r.latest_version is None for r in results))

    async def test_constraints(self):
        for run_id, name, status in [(999, "a", "low"), (1, "a", "invalid")]:
            with self.assertRaises(IntegrityError):
                async with self.sessions.begin() as session:
                    session.add(
                        DependencyResult(
                            run_id=run_id, name=name, current_version="1", status=status
                        )
                    )
        with self.assertRaises(IntegrityError):
            async with self.sessions.begin() as session:
                session.add_all(
                    [
                        DependencyResult(
                            run_id=1, name="a", current_version="1", status="low"
                        )
                        for _ in range(2)
                    ]
                )
