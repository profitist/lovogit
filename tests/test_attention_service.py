from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from helpers import DatabaseCase
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError

from lovogit.app.services.attention import AttentionService, AttentionUnavailable
from lovogit.app.services.monitoring import DependencyResultDTO, MonitoringStateService
from lovogit.db import (
    DependencyResult,
    DependencyStatus,
    MonitoringRun,
    RepoModel,
    RunState,
)


class AttentionServiceTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.state = MonitoringStateService(self.sessions)

    async def overview(self):
        async with self.sessions() as session:
            return await AttentionService(session).get_overview()

    async def test_selection_order_projects_and_snapshot_not_inventory(self):
        async with self.sessions.begin() as session:
            session.add_all(
                [
                    RepoModel(id=2, name="backend", repo_url="url"),
                    RepoModel(id=3, name="aaa", repo_url="url"),
                    RepoModel(
                        id=4,
                        name="deleted",
                        repo_url="url",
                        deleted_at=datetime.now(UTC),
                    ),
                ]
            )
        for project_id in (1, 2, 4):
            run = await self.state.start_run(project_id)
            await self.state.succeed_run(
                run,
                [
                    DependencyResultDTO("z", "1", "2", "incompat"),
                    DependencyResultDTO("a", "3", None, "review"),
                    DependencyResultDTO("b", "1", "2", "review"),
                    DependencyResultDTO("c", "1", "2", "failed"),
                    *[
                        DependencyResultDTO(status, "1", "2", status)
                        for status in ("waiting", "low", "current")
                    ],
                ],
            )
        projects = await self.overview()
        self.assertEqual([p.project_id for p in projects], [3, 1, 2])
        self.assertIsNone(projects[0].snapshot_id)
        for project in projects[1:]:
            self.assertEqual(
                [d.name for d in project.dependencies], ["z", "a", "b", "c"]
            )
            self.assertEqual(
                [d.status for d in project.dependencies],
                [
                    DependencyStatus.INCOMPAT,
                    DependencyStatus.REVIEW,
                    DependencyStatus.REVIEW,
                    DependencyStatus.FAILED,
                ],
            )
            self.assertEqual(project.dependencies[1].current_version, "3")
            self.assertIsNone(project.dependencies[1].latest_version)
            self.assertEqual(project.latest_snapshot_at.tzinfo, UTC)
            self.assertEqual(project.latest_run.started_at.tzinfo, UTC)
        async with self.sessions.begin() as session:
            await session.execute(
                update(RepoModel).values(deleted_at=datetime.now(UTC))
            )
        self.assertEqual(await self.overview(), [])

    async def test_missing_empty_and_unselected_snapshots(self):
        project = (await self.overview())[0]
        self.assertIsNone(project.latest_run)
        self.assertIsNone(project.snapshot_id)
        self.assertEqual(project.dependencies, ())
        failed = await self.state.start_run(1)
        await self.state.fail_run(failed, "Источник недоступен")
        project = (await self.overview())[0]
        self.assertEqual(project.latest_run.state, RunState.FAILED)
        self.assertIsNone(project.snapshot_id)
        for results in ([], [DependencyResultDTO("a", "1", "2", "waiting")]):
            run = await self.state.start_run(1)
            await self.state.succeed_run(run, results)
            project = (await self.overview())[0]
            self.assertEqual(project.snapshot_id, run)
            self.assertEqual(project.dependencies, ())

    async def test_failure_and_running_keep_snapshot(self):
        run = await self.state.start_run(1)
        await self.state.succeed_run(
            run, [DependencyResultDTO("a", "1", "2", "review")]
        )
        failed = await self.state.start_run(1)
        await self.state.fail_run(failed, "Источник недоступен")
        project = (await self.overview())[0]
        self.assertEqual(project.snapshot_id, run)
        self.assertEqual(project.latest_run.error, "Источник недоступен")
        running = await self.state.start_run(1)
        project = (await self.overview())[0]
        self.assertEqual(project.latest_run.run_id, running)
        self.assertEqual(project.latest_run.state, RunState.RUNNING)
        self.assertEqual(project.snapshot_id, run)
        self.assertEqual([d.name for d in project.dependencies], ["a"])

    async def test_start_time_and_id_decide_snapshot_not_finish_time(self):
        time = datetime(2026, 1, 1, tzinfo=UTC)
        older = await self.state.start_run(1, started_at=time)
        newer = await self.state.start_run(1, started_at=time + timedelta(seconds=1))
        await self.state.succeed_run(
            newer, [DependencyResultDTO("new", "1", "2", "review")]
        )
        await self.state.succeed_run(
            older, [DependencyResultDTO("old", "1", "2", "incompat")]
        )
        self.assertEqual((await self.overview())[0].snapshot_id, newer)
        tied = await self.state.start_run(1, started_at=time + timedelta(seconds=1))
        await self.state.succeed_run(tied, [])
        self.assertEqual((await self.overview())[0].snapshot_id, tied)
        self.assertEqual((await self.overview())[0].dependencies, ())

    async def test_single_read_query_and_unchanged_history(self):
        run = await self.state.start_run(1)
        await self.state.succeed_run(
            run, [DependencyResultDTO("a", "1", "2", "review")]
        )
        async with self.sessions.begin() as session:
            session.add_all(
                [
                    RepoModel(id=i, name=f"project-{i}", repo_url="url")
                    for i in range(2, 12)
                ]
            )
        async with self.sessions() as session:
            before_runs = (await session.execute(select(MonitoringRun.__table__))).all()
            before_results = (
                await session.execute(select(DependencyResult.__table__))
            ).all()
        queries = []

        def record(*args):
            queries.append(args[2])

        event.listen(self.engine.sync_engine, "before_cursor_execute", record)
        try:
            self.assertEqual(len(await self.overview()), 11)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", record)
        self.assertEqual(len(queries), 1)
        self.assertTrue(queries[0].lstrip().upper().startswith("SELECT"))
        async with self.sessions() as session:
            self.assertEqual(
                (await session.execute(select(MonitoringRun.__table__))).all(),
                before_runs,
            )
            self.assertEqual(
                (await session.execute(select(DependencyResult.__table__))).all(),
                before_results,
            )

    async def test_storage_error_preserves_cause(self):
        error = OperationalError("SQL", {}, Exception("offline"))
        session = AsyncMock()
        session.execute.side_effect = error
        with self.assertRaises(AttentionUnavailable) as captured:
            await AttentionService(session).get_overview()
        self.assertIs(captured.exception.__cause__, error)
