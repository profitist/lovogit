import asyncio
from datetime import UTC, datetime, timedelta

from helpers import DatabaseCase
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

from lovogit.app.services.monitoring import (
    DependencyResultDTO,
    MonitoringStateService,
    RunTransitionError,
)
from lovogit.db import DependencyResult, DependencyStatus, MonitoringRun, RunState

RESULT = DependencyResultDTO("Some_Package.name", "1", "2", DependencyStatus.LOW)


class MonitoringTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.service = MonitoringStateService(self.sessions)

    async def test_success_failure_and_empty_snapshot_persist(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        run = await self.service.start_run(1, started_at=start)
        await self.service.succeed_run(
            run, [RESULT], finished_at=start + timedelta(seconds=1)
        )
        failed = await self.service.start_run(1)
        await self.service.fail_run(failed, "Источник недоступен")
        empty = await self.service.start_run(1)
        await self.service.succeed_run(empty, [])
        await self.engine.dispose()
        async with self.sessions() as session:
            runs = (
                await session.scalars(select(MonitoringRun).order_by(MonitoringRun.id))
            ).all()
            self.assertEqual(
                [r.state for r in runs],
                [RunState.SUCCEEDED, RunState.FAILED, RunState.SUCCEEDED],
            )
            self.assertEqual(runs[0].started_at, start.replace(tzinfo=None))
            self.assertEqual(runs[1].error, "Источник недоступен")
            results = (await session.scalars(select(DependencyResult))).all()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].name, "some-package-name")

    async def test_invalid_data_and_repeat_completion(self):
        run = await self.service.start_run(1)
        for invalid in (
            [RESULT, DependencyResultDTO("some.package_name", "1", "2", "low")],
            [DependencyResultDTO("a", "1", "2", "wrong")],
        ):
            with self.assertRaises(ValueError):
                await self.service.succeed_run(run, invalid)
        with self.assertRaises(ValueError):
            await self.service.fail_run(run, "   ")
        with self.assertRaises(ValueError):
            await self.service.start_run(1, started_at=datetime(2026, 1, 1))
        await self.service.succeed_run(run, [])
        for action in (
            self.service.succeed_run(run, []),
            self.service.fail_run(run, "Причина"),
        ):
            with self.assertRaises(RunTransitionError):
                await action

    async def test_insert_failure_rolls_back_completion_and_all_results(self):
        run = await self.service.start_run(1)
        inserted = 0

        def fail_second_insert(connection, cursor, statement, params, context, many):
            nonlocal inserted
            if statement.startswith("INSERT INTO dependency_results"):
                inserted += 1
                if inserted == 2:
                    raise RuntimeError("Имитированный сбой записи")

        event.listen(
            self.engine.sync_engine, "before_cursor_execute", fail_second_insert
        )
        try:
            with self.assertRaises(RuntimeError):
                await self.service.succeed_run(
                    run, [RESULT, DependencyResultDTO("other", "1", None, "current")]
                )
        finally:
            event.remove(
                self.engine.sync_engine, "before_cursor_execute", fail_second_insert
            )
        async with self.sessions() as session:
            self.assertEqual(
                (await session.get(MonitoringRun, run)).state, RunState.RUNNING
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count()).select_from(DependencyResult)
                ),
                0,
            )

    async def test_concurrent_completion_has_one_winner(self):
        run = await self.service.start_run(1)
        outcomes = await asyncio.gather(
            self.service.succeed_run(run, [RESULT]),
            self.service.fail_run(run, "Источник недоступен"),
            return_exceptions=True,
        )
        self.assertEqual(
            sum(isinstance(outcome, RunTransitionError) for outcome in outcomes), 1
        )
        self.assertEqual(outcomes.count(None), 1)
        async with self.sessions() as session:
            state = (await session.get(MonitoringRun, run)).state
            count = await session.scalar(
                select(func.count()).select_from(DependencyResult)
            )
            self.assertEqual(count, 1 if state == RunState.SUCCEEDED else 0)

    async def test_unknown_project_and_invalid_time_rejected(self):
        with self.assertRaises(IntegrityError):
            await self.service.start_run(999)
        run = await self.service.start_run(1)
        with self.assertRaises(IntegrityError):
            await self.service.succeed_run(
                run, [], finished_at=datetime(2000, 1, 1, tzinfo=UTC)
            )
        async with self.sessions() as session:
            self.assertEqual(
                (await session.get(MonitoringRun, run)).state, RunState.RUNNING
            )
