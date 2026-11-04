import asyncio
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from aiogram import Bot, Dispatcher
from aiogram.types import Message, Update
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_status_middleware import message

from lovogit.app.providers.github import GitHubProvider
from lovogit.app.services.attention import AttentionService
from lovogit.app.services.monitoring import DependencyResultDTO, MonitoringStateService
from lovogit.app.services.registration import RepositoryRegistrationService
from lovogit.app.services.status import StatusService
from lovogit.bot.add_repo import create_registration_router
from lovogit.bot.attention import create_attention_router
from lovogit.bot.status import create_status_router
from lovogit.cfg.github import GitHubSettings
from lovogit.db import DependencyModel, DependencyResult, MonitoringRun, RepoModel
from lovogit.db.engine import make_engine

ROOT = Path(__file__).resolve().parents[1]


class RegistrationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_migrated_http_registration_restore_read_commands_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = f"sqlite+aiosqlite:///{Path(tmp) / 'registration.sqlite3'}"
            migrated = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=ROOT,
                env={
                    **os.environ,
                    "DATABASE_URL": url,
                    "PYTHONPATH": str(ROOT / "src"),
                },
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            engine = make_engine(url)
            self.addAsyncCleanup(engine.dispose)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            requests = []

            def github(request):
                requests.append(request.url.path)
                self.assertEqual(request.headers["Authorization"], "Bearer fake-token")
                if request.url.path == "/repos/team/project":
                    return httpx.Response(
                        200,
                        json={
                            "id": 123,
                            "full_name": "team/project",
                            "default_branch": "main",
                        },
                    )
                if request.url.path == "/repos/team/project/commits/main":
                    return httpx.Response(200, json={"sha": "a" * 40})
                self.fail(f"Unexpected request: {request.url.path}")

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(github)
            ) as client:
                provider = GitHubProvider(
                    client, GitHubSettings(GITHUB_TOKEN="fake-token", _env_file=None)
                )
                service = RepositoryRegistrationService(provider, sessions)
                bot = Bot("123456:fake-token")
                self.addAsyncCleanup(bot.session.close)
                dispatcher = Dispatcher()
                general = runpy.run_path(str(ROOT / "src/lovogit/bot/commands.py"))[
                    "router"
                ]
                dispatcher.include_routers(
                    general,
                    create_registration_router([-100], service),
                    create_status_router([-100], sessions),
                    create_attention_router([-100], sessions),
                )
                outputs = []
                with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                    for index, text in enumerate(
                        (
                            "/add_repo https://github.com/team/project.git/",
                            "/add_repo https://github.com/team/project",
                            "/status",
                            "/attention",
                        )
                    ):
                        answer.reset_mock()
                        await dispatcher.feed_update(
                            bot,
                            Update(
                                update_id=index + 1,
                                message=message().model_copy(update={"text": text}),
                            ),
                        )
                        outputs.append(
                            "".join(call.args[0] for call in answer.await_args_list)
                        )
                        self.assertTrue(
                            all(
                                call.kwargs == {"parse_mode": None}
                                for call in answer.await_args_list
                            )
                        )
                self.assertIn("Репозиторий добавлен", outputs[0])
                self.assertIn("Репозиторий уже отслеживается", outputs[1])
                self.assertEqual(
                    outputs[0].splitlines()[1:], outputs[1].splitlines()[1:]
                )
                for text in outputs[2:]:
                    self.assertIn("team/project (ID: 1)", text)
                    self.assertIn("Оценки обновлений пока нет", text)
                async with sessions() as session:
                    self.assertEqual(
                        await session.scalar(
                            select(func.count()).select_from(RepoModel)
                        ),
                        1,
                    )
                    for model in (DependencyModel, MonitoringRun, DependencyResult):
                        self.assertEqual(
                            await session.scalar(
                                select(func.count()).select_from(model)
                            ),
                            0,
                        )
                await engine.dispose()
                async with sessions() as session:
                    self.assertEqual((await session.get(RepoModel, 1)).github_id, 123)
                state = MonitoringStateService(sessions)
                run = await state.start_run(1)
                await state.succeed_run(
                    run, [DependencyResultDTO("requests", "1", "2", "review")]
                )
                async with sessions.begin() as session:
                    repo = await session.get(RepoModel, 1)
                    repo.deleted_at = repo.created_at
                    session.add(
                        DependencyModel(repo_id=1, name="requests", current_version="1")
                    )
                with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                    await dispatcher.feed_update(
                        bot,
                        Update(
                            update_id=10,
                            message=message().model_copy(
                                update={
                                    "text": "/add_repo https://github.com/team/project"
                                }
                            ),
                        ),
                    )
                    self.assertIn(
                        "Отслеживание восстановлено", answer.await_args.args[0]
                    )
                await engine.dispose()
                async with sessions() as session:
                    restored = (await StatusService(session).get_overview())[0]
                    attention = (await AttentionService(session).get_overview())[0]
                    self.assertEqual(restored.latest_run.run_id, run)
                    self.assertEqual(restored.dependencies_count, 1)
                    self.assertEqual(attention.dependencies[0].name, "requests")
                    self.assertEqual(attention.snapshot_id, run)
                    self.assertEqual(
                        await session.scalar(
                            select(func.count()).select_from(MonitoringRun)
                        ),
                        1,
                    )
                    self.assertEqual(
                        await session.scalar(
                            select(func.count()).select_from(DependencyResult)
                        ),
                        1,
                    )
                self.assertEqual(len(requests), 6)
