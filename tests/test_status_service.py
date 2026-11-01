from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from helpers import DatabaseCase
from sqlalchemy import event, update
from sqlalchemy.exc import OperationalError

from lovogit.app.services.monitoring import DependencyResultDTO, MonitoringStateService
from lovogit.app.services.status import StatusService, StatusUnavailable
from lovogit.db import DependencyModel, DependencyStatus, RepoModel, RunState


class StatusTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.state = MonitoringStateService(self.sessions)

    async def overview(self):
        async with self.sessions() as session:
            return await StatusService(session).get_overview()

    async def test_no_runs_empty_projects_soft_delete_and_stable_order(self):
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
                    DependencyModel(repo_id=1, name="a", current_version="1"),
                    DependencyModel(
                        repo_id=1,
                        name="b",
                        current_version="1",
                        deleted_at=datetime.now(UTC),
                    ),
                ]
            )
        queries = []

        def record(*args):
            queries.append(args[2])

        event.listen(self.engine.sync_engine, "before_cursor_execute", record)
        try:
            overview = await self.overview()
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", record)
        self.assertEqual(len(queries), 1)
        self.assertEqual([p.project_id for p in overview], [3, 1, 2])
        backend = overview[1]
        self.assertEqual(backend.dependencies_count, 1)
        self.assertIsNone(backend.latest_run)
        self.assertIsNone(backend.status_counts)
        self.assertIsNone(backend.latest_snapshot_at)
        async with self.sessions.begin() as session:
            await session.execute(
                update(RepoModel).values(deleted_at=datetime.now(UTC))
            )
        self.assertEqual(await self.overview(), [])

    async def test_all_six_counts_empty_snapshot_and_current_dependency_count(self):
        run = await self.state.start_run(1)
        await self.state.succeed_run(
            run,
            [
                DependencyResultDTO(
                    status, "1", None if status == "current" else "2", status
                )
                for status in DependencyStatus
            ],
        )
        project = (await self.overview())[0]
        self.assertEqual(project.status_counts, dict.fromkeys(DependencyStatus, 1))
        self.assertEqual(project.dependencies_count, 0)
        self.assertEqual(project.latest_run.state, RunState.SUCCEEDED)
        self.assertEqual(project.latest_snapshot_at.tzinfo, UTC)
        empty = await self.state.start_run(1)
        await self.state.succeed_run(empty, [])
        self.assertEqual(
            (await self.overview())[0].status_counts, dict.fromkeys(DependencyStatus, 0)
        )

    async def test_failure_and_running_keep_previous_snapshot(self):
        success = await self.state.start_run(1)
        await self.state.succeed_run(
            success, [DependencyResultDTO("a", "1", "2", "review")]
        )
        snapshot_time = (await self.overview())[0].latest_snapshot_at
        failed = await self.state.start_run(1)
        await self.state.fail_run(failed, "Источник недоступен")
        project = (await self.overview())[0]
        self.assertEqual(project.latest_run.state, RunState.FAILED)
        self.assertEqual(project.latest_run.error, "Источник недоступен")
        self.assertEqual(project.latest_snapshot_at, snapshot_time)
        self.assertEqual(project.status_counts[DependencyStatus.REVIEW], 1)
        running = await self.state.start_run(1)
        project = (await self.overview())[0]
        self.assertEqual(project.latest_run.run_id, running)
        self.assertEqual(project.latest_run.state, RunState.RUNNING)
        self.assertEqual(project.latest_snapshot_at, snapshot_time)

    async def test_first_failure_has_no_counters(self):
        run = await self.state.start_run(1)
        await self.state.fail_run(run, "Источник недоступен")
        project = (await self.overview())[0]
        self.assertEqual(project.latest_run.state, RunState.FAILED)
        self.assertIsNone(project.status_counts)

    async def test_out_of_order_completion_and_other_project_failure(self):
        async with self.sessions.begin() as session:
            session.add(RepoModel(id=2, name="worker", repo_url="url"))
        time = datetime(2026, 1, 1, tzinfo=UTC)
        older = await self.state.start_run(1, started_at=time)
        newer = await self.state.start_run(1, started_at=time + timedelta(seconds=1))
        await self.state.succeed_run(
            newer, [DependencyResultDTO("new", "1", None, "current")]
        )
        await self.state.succeed_run(
            older, [DependencyResultDTO("old", "1", "2", "incompat")]
        )
        failed = await self.state.start_run(2)
        await self.state.fail_run(failed, "Ошибка источника")
        projects = await self.overview()
        self.assertEqual(len(projects), 2)
        self.assertEqual(projects[0].latest_run.run_id, newer)
        self.assertEqual(projects[0].status_counts[DependencyStatus.CURRENT], 1)
        self.assertEqual(projects[0].status_counts[DependencyStatus.INCOMPAT], 0)
        self.assertEqual(projects[1].latest_run.state, RunState.FAILED)

    async def test_equal_start_time_uses_id(self):
        time = datetime(2026, 1, 1, tzinfo=UTC)
        older = await self.state.start_run(1, started_at=time)
        newer = await self.state.start_run(1, started_at=time)
        await self.state.succeed_run(newer, [])
        await self.state.succeed_run(older, [DependencyResultDTO("a", "1", "2", "low")])
        project = (await self.overview())[0]
        self.assertEqual(project.latest_run.run_id, newer)
        self.assertEqual(sum(project.status_counts.values()), 0)

    async def test_storage_error_preserves_cause(self):
        error = OperationalError("SQL", {}, Exception("database offline"))
        session = AsyncMock()
        session.execute.side_effect = error
        with self.assertRaises(StatusUnavailable) as captured:
            await StatusService(session).get_overview()
        self.assertIs(captured.exception.__cause__, error)
