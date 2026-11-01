"""Запись истории проверок без привязки к транспорту или исполнителю."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lovogit.db import DependencyResult, DependencyStatus, MonitoringRun, RunState


class RunTransitionError(Exception):
    """Запуск отсутствует либо уже завершён."""


def utc_naive(value: datetime) -> datetime:
    """SQLite хранит UTC без смещения, наружу время возвращается с UTC."""
    if value.tzinfo is None:
        raise ValueError("Требуется время с часовым поясом")
    return value.astimezone(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class DependencyResultDTO:
    name: str
    current_version: str
    latest_version: str | None
    status: DependencyStatus

    def normalized(self):
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", self.name):
            raise ValueError("Некорректное имя Python-пакета")
        return DependencyResultDTO(
            re.sub(r"[-_.]+", "-", self.name).lower(),
            self.current_version,
            self.latest_version,
            DependencyStatus(self.status),
        )


class MonitoringStateService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def start_run(
        self, project_id: int, *, started_at: datetime | None = None
    ) -> int:
        async with self.sessions.begin() as session:
            run = MonitoringRun(
                repo_id=project_id,
                started_at=utc_naive(started_at or datetime.now(UTC)),
                state=RunState.RUNNING,
            )
            session.add(run)
            await session.flush()
            return run.id

    async def succeed_run(
        self,
        run_id: int,
        results: list[DependencyResultDTO],
        *,
        finished_at: datetime | None = None,
    ) -> None:
        normalized = [result.normalized() for result in results]
        if len({result.name for result in normalized}) != len(normalized):
            raise ValueError("Повторяющиеся имена зависимостей")
        async with self.sessions.begin() as session:
            await self._finish(session, run_id, RunState.SUCCEEDED, finished_at, None)
            session.add_all(
                [
                    DependencyResult(
                        run_id=run_id,
                        name=result.name,
                        current_version=result.current_version,
                        latest_version=result.latest_version,
                        status=result.status,
                    )
                    for result in normalized
                ]
            )
            await session.flush()

    async def fail_run(
        self,
        run_id: int,
        reason: str,
        *,
        finished_at: datetime | None = None,
    ) -> None:
        """reason — пользовательское объяснение без токенов, SQL и трассировок."""
        if not reason.strip():
            raise ValueError("Требуется причина ошибки")
        async with self.sessions.begin() as session:
            await self._finish(session, run_id, RunState.FAILED, finished_at, reason)

    @staticmethod
    async def _finish(session, run_id, state, finished_at, error):
        result = await session.execute(
            update(MonitoringRun)
            .where(MonitoringRun.id == run_id, MonitoringRun.state == RunState.RUNNING)
            .values(
                state=state,
                finished_at=utc_naive(finished_at or datetime.now(UTC)),
                error=error,
            )
        )
        if result.rowcount != 1:
            raise RunTransitionError("Запуск отсутствует либо уже завершён")
