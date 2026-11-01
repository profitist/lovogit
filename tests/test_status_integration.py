import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.types import Message, Update
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_status_middleware import message

from lovogit.app.services.monitoring import DependencyResultDTO, MonitoringStateService
from lovogit.app.services.status import StatusService
from lovogit.bot.status import create_status_router
from lovogit.db import DependencyModel, DependencyStatus, RepoModel, RunState
from lovogit.db.engine import make_engine

ROOT = Path(__file__).resolve().parents[1]


class StatusIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_migrated_database_write_read_dispatch_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = f"sqlite+aiosqlite:///{Path(tmp) / 'integration.sqlite3'}"
            migration = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=ROOT,
                check=False,
                env={
                    **os.environ,
                    "DATABASE_URL": url,
                    "PYTHONPATH": str(ROOT / "src"),
                },
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(migration.returncode, 0, migration.stderr)
            engine = make_engine(url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions.begin() as session:
                    session.add_all(
                        [
                            RepoModel(id=1, name="backend", repo_url="url"),
                            RepoModel(id=2, name="worker", repo_url="url"),
                        ]
                    )
                async with sessions.begin() as session:
                    session.add(
                        DependencyModel(repo_id=1, name="requests", current_version="1")
                    )
                state = MonitoringStateService(sessions)
                run = await state.start_run(1)
                await state.succeed_run(
                    run, [DependencyResultDTO("requests", "1", "2", "review")]
                )
                failed = await state.start_run(2)
                await state.fail_run(failed, "Не удалось получить сведения о релизах")
                bot = Bot("123456:fake-token")
                try:
                    dispatcher = Dispatcher()
                    dispatcher.include_router(create_status_router([-100], sessions))
                    rendered = []
                    for i in range(2):
                        async with sessions() as session:
                            overview = await StatusService(session).get_overview()
                        self.assertEqual(
                            [p.project_name for p in overview], ["backend", "worker"]
                        )
                        self.assertEqual(
                            overview[0].status_counts[DependencyStatus.REVIEW], 1
                        )
                        self.assertEqual(overview[1].latest_run.state, RunState.FAILED)
                        self.assertIsNone(overview[1].status_counts)
                        with patch.object(
                            Message, "answer", new_callable=AsyncMock
                        ) as answer:
                            await dispatcher.feed_update(
                                bot, Update(update_id=i + 1, message=message())
                            )
                        rendered.append(
                            "".join(call.args[0] for call in answer.await_args_list)
                        )
                        self.assertTrue(
                            all(
                                call.kwargs["parse_mode"] is None
                                for call in answer.await_args_list
                            )
                        )
                        await engine.dispose()
                    self.assertEqual(rendered[0], rendered[1])
                    self.assertIn("Требует проверки: 1", rendered[0])
                    self.assertIn(
                        "Причина: Не удалось получить сведения о релизах", rendered[0]
                    )
                finally:
                    await bot.session.close()
            finally:
                await engine.dispose()
