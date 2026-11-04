import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from helpers import DatabaseCase
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from lovogit.app.providers.dto import ResolvedRepoDTO
from lovogit.app.providers.errors import (
    AuthenticationError,
    InvalidRepoURL,
    InvalidSourceResponse,
    RateLimited,
    RepoNotFound,
    RevisionUnavailable,
    SourceUnavailable,
)
from lovogit.app.providers.github import GitHubProvider
from lovogit.app.services.monitoring import DependencyResultDTO, MonitoringStateService
from lovogit.app.services.registration import (
    RegistrationConflict,
    RegistrationOutcome,
    RegistrationUnavailable,
    RepositoryRegistrationService,
)
from lovogit.db import DependencyModel, DependencyResult, MonitoringRun, RepoModel

URL = "https://github.com/team/project"
RESOLVED = ResolvedRepoDTO(123, "team/project", URL, "main", "a" * 40)


class RegistrationServiceTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.provider = AsyncMock(spec=GitHubProvider)
        self.provider.resolve.return_value = RESOLVED
        self.service = RepositoryRegistrationService(self.provider, self.sessions)

    async def test_add_persist_and_repeat_without_analysis(self):
        result = await self.service.add_repository(URL + ".git/")
        self.assertEqual(result.outcome, RegistrationOutcome.ADDED)
        self.assertEqual((result.project_name, result.repo_url), ("team/project", URL))
        await self.engine.dispose()
        async with self.sessions() as session:
            repo = await session.get(RepoModel, result.project_id)
            self.assertEqual(repo.github_id, 123)
            for model in (MonitoringRun, DependencyResult, DependencyModel):
                self.assertEqual((await session.scalars(select(model))).all(), [])
        for url in (
            URL.upper().replace("HTTPS", "https"),
            URL,
            URL + ".git",
            URL + "/",
        ):
            repeated = await self.service.add_repository(url)
            self.assertEqual(repeated.project_id, result.project_id)
            self.assertEqual(repeated.outcome, RegistrationOutcome.ALREADY_TRACKED)
        self.provider.snapshot.assert_not_called()
        async with self.sessions() as session:
            self.assertEqual(len((await session.scalars(select(RepoModel))).all()), 2)

    async def test_rename_same_id_and_attach_legacy_url(self):
        async with self.sessions.begin() as session:
            repo = await session.get(RepoModel, 1)
            repo.repo_url = "https://github.com/TEAM/PROJECT.git/"
        result = await self.service.add_repository(URL)
        self.assertEqual(result.project_id, 1)
        self.assertEqual(result.outcome, RegistrationOutcome.ALREADY_TRACKED)
        self.provider.resolve.return_value = ResolvedRepoDTO(
            123, "new/name", "https://github.com/new/name", "main", "b" * 40
        )
        renamed = await self.service.add_repository(URL)
        self.assertEqual(renamed.project_id, 1)
        self.assertEqual(renamed.project_name, "new/name")
        async with self.sessions() as session:
            self.assertEqual((await session.get(RepoModel, 1)).github_id, 123)

    async def test_restore_preserves_dependencies_and_history(self):
        run = await MonitoringStateService(self.sessions).start_run(1)
        await MonitoringStateService(self.sessions).succeed_run(
            run, [DependencyResultDTO("requests", "1", "2", "review")]
        )
        async with self.sessions.begin() as session:
            repo = await session.get(RepoModel, 1)
            repo.github_id = 123
            repo.deleted_at = datetime.now(UTC)
            session.add(
                DependencyModel(repo_id=1, name="requests", current_version="1")
            )
        result = await self.service.add_repository(URL)
        self.assertEqual(result.project_id, 1)
        self.assertEqual(result.outcome, RegistrationOutcome.RESTORED)
        async with self.sessions() as session:
            self.assertIsNone((await session.get(RepoModel, 1)).deleted_at)
            self.assertEqual((await session.get(MonitoringRun, run)).state, "succeeded")
            self.assertEqual(
                (await session.scalars(select(DependencyResult))).one().name, "requests"
            )
            self.assertEqual(
                (await session.scalars(select(DependencyModel))).one().name, "requests"
            )

    async def test_legacy_conflicts_rollback_without_merging(self):
        for github_ids in ((None, None), (123, None), (999, None)):
            with self.subTest(github_ids=github_ids):
                async with self.sessions.begin() as session:
                    first = await session.get(RepoModel, 1)
                    first.repo_url, first.github_id = URL, github_ids[0]
                    second = await session.get(RepoModel, 2)
                    if second is None:
                        second = RepoModel(id=2, name="duplicate", repo_url=URL)
                        session.add(second)
                    second.github_id = github_ids[1]
                with self.assertRaises(RegistrationConflict):
                    await self.service.add_repository(URL)
                async with self.sessions() as session:
                    self.assertEqual((await session.get(RepoModel, 1)).name, "backend")
                    self.assertEqual(
                        (await session.get(RepoModel, 2)).name, "duplicate"
                    )
        async with self.sessions.begin() as session:
            await session.delete(await session.get(RepoModel, 2))
        with self.assertRaises(RegistrationConflict):
            await self.service.add_repository(URL)

    async def test_concurrent_add_and_network_before_database(self):
        calls = []

        def record(*args):
            calls.append(args[2])

        event.listen(self.engine.sync_engine, "before_cursor_execute", record)
        barrier = asyncio.Event()
        resolved_count = 0

        async def resolve(url):
            nonlocal resolved_count
            self.assertEqual(calls, [])
            resolved_count += 1
            if resolved_count == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), 2)
            return RESOLVED

        self.provider.resolve.side_effect = resolve
        try:
            results = await asyncio.gather(
                self.service.add_repository(URL), self.service.add_repository(URL)
            )
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", record)
        self.assertEqual(results[0].project_id, results[1].project_id)
        self.assertEqual(
            {r.outcome for r in results},
            {RegistrationOutcome.ADDED, RegistrationOutcome.ALREADY_TRACKED},
        )
        async with self.sessions() as session:
            self.assertEqual(
                len(
                    (
                        await session.scalars(
                            select(RepoModel).where(RepoModel.github_id == 123)
                        )
                    ).all()
                ),
                1,
            )

    async def test_invalid_input_and_provider_failures_do_not_touch_database(self):
        with self.assertRaises(InvalidRepoURL):
            await self.service.add_repository("https://example.org/x")
        self.provider.resolve.assert_not_called()
        for error in (
            AuthenticationError(),
            RepoNotFound(),
            RevisionUnavailable(),
            RateLimited(),
            SourceUnavailable(),
            InvalidSourceResponse(),
        ):
            self.provider.resolve.side_effect = error
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                await self.service.add_repository(URL)
            async with self.sessions() as session:
                repos = (await session.scalars(select(RepoModel))).all()
                self.assertEqual(
                    [(r.id, r.name, r.github_id) for r in repos], [(1, "backend", None)]
                )

    async def test_storage_error_rolls_back_and_preserves_cause(self):
        error = OperationalError("private SQL", {}, Exception("offline"))
        with (
            patch("sqlalchemy.ext.asyncio.AsyncSession.flush", side_effect=error),
            self.assertRaises(RegistrationUnavailable) as captured,
        ):
            await self.service.add_repository(URL)
        self.assertIs(captured.exception.__cause__, error)
        async with self.sessions() as session:
            self.assertEqual(len((await session.scalars(select(RepoModel))).all()), 1)
