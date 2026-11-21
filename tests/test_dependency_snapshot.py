import asyncio
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from lovogit.app.dependencies.inventory import read_dependency_inventory
from lovogit.app.providers.github.client import GitHubProvider
from lovogit.app.services.repo import RepoService
from lovogit.cfg.github import GitHubSettings
from lovogit.db import DependencyModel, MonitoringRun, RepoModel
from lovogit.db.engine import Base, make_engine


class DependencySnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_documented_path_keeps_snapshot_identity_and_database_unchanged(self):
        engine = make_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(engine.dispose)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            session.add(
                RepoModel(
                    id=1, name="project", repo_url="https://github.com/team/project"
                )
            )
        async with sessions.begin() as session:
            session.add(
                DependencyModel(repo_id=1, name="old-package", current_version="1.0")
            )
            session.add(
                MonitoringRun(
                    repo_id=1, started_at=datetime(2026, 1, 1), state="running"
                )
            )
        stream = io.BytesIO()
        with ZipFile(stream, "w") as archive:
            archive.writestr("prefix/requirements.txt", "httpx==0.28.0\n")
            archive.writestr(
                "prefix/setup.py", "raise RuntimeError('must not execute')\n"
            )
        sha = "b" * 40

        def handler(request):
            if "/commits/" in request.url.path:
                return httpx.Response(200, json={"sha": sha})
            if "/zipball/" in request.url.path:
                return httpx.Response(200, content=stream.getvalue())
            return httpx.Response(
                200,
                json={"id": 42, "full_name": "team/project", "default_branch": "main"},
            )

        with tempfile.TemporaryDirectory() as directory:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                provider = GitHubProvider(
                    client,
                    GitHubSettings(
                        _env_file=None,
                        GITHUB_TOKEN="test",
                        GITHUB_WORKSPACE_DIR=directory,
                    ),
                )
                repo_service = RepoService(provider)
                async with repo_service.open_source(
                    "https://github.com/team/project"
                ) as snapshot:
                    root = snapshot.root
                    before = {
                        str(path.relative_to(root)): path.read_bytes()
                        for path in root.rglob("*")
                        if path.is_file()
                    }
                    with patch(
                        "sqlalchemy.ext.asyncio.AsyncSession.execute",
                        side_effect=AssertionError(
                            "Inventory must not access database"
                        ),
                    ):
                        result = await asyncio.to_thread(
                            read_dependency_inventory, snapshot
                        )
                    after = {
                        str(path.relative_to(root)): path.read_bytes()
                        for path in root.rglob("*")
                        if path.is_file()
                    }
                    self.assertEqual(before, after)
                self.assertFalse(root.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual((result.repo.repo_id, result.repo.commit_sha), (42, sha))
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.dependencies[0].current_version, "0.28.0")
        self.assertEqual(result.dependencies[0].origins[0].path, "requirements.txt")
        async with sessions() as session:
            dependency = (await session.scalars(select(DependencyModel))).one()
            run = (await session.scalars(select(MonitoringRun))).one()
            self.assertEqual(
                (dependency.name, dependency.current_version), ("old-package", "1.0")
            )
            self.assertEqual(run.state, "running")
