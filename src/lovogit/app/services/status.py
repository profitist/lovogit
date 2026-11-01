"""Чтение общей сводки проектов экземпляра одним запросом."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from lovogit.db import (
    DependencyModel,
    DependencyResult,
    DependencyStatus,
    MonitoringRun,
    RepoModel,
    RunState,
)


class StatusUnavailable(Exception):
    """Не удалось прочитать состояние мониторинга."""


def as_utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None else None


@dataclass(frozen=True)
class CheckSummaryDTO:
    run_id: int
    state: RunState
    started_at: datetime
    finished_at: datetime | None
    error: str | None


@dataclass(frozen=True)
class ProjectStatusDTO:
    project_id: int
    project_name: str
    dependencies_count: int
    latest_run: CheckSummaryDTO | None
    latest_snapshot_at: datetime | None
    status_counts: dict[DependencyStatus, int] | None


class StatusService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_overview(self) -> list[ProjectStatusDTO]:
        latest = aliased(MonitoringRun)
        snapshot = aliased(MonitoringRun)
        latest_id = (
            select(MonitoringRun.id)
            .where(MonitoringRun.repo_id == RepoModel.id)
            .order_by(MonitoringRun.started_at.desc(), MonitoringRun.id.desc())
            .limit(1)
            .correlate(RepoModel)
            .scalar_subquery()
        )
        snapshot_id = (
            select(MonitoringRun.id)
            .where(
                MonitoringRun.repo_id == RepoModel.id,
                MonitoringRun.state == RunState.SUCCEEDED,
            )
            .order_by(MonitoringRun.started_at.desc(), MonitoringRun.id.desc())
            .limit(1)
            .correlate(RepoModel)
            .scalar_subquery()
        )
        dependencies = (
            select(DependencyModel.repo_id, func.count().label("count"))
            .where(DependencyModel.deleted_at.is_(None))
            .group_by(DependencyModel.repo_id)
            .subquery()
        )
        counts = (
            select(
                DependencyResult.run_id,
                *[
                    func.sum(
                        case((DependencyResult.status == status, 1), else_=0)
                    ).label(status.value)
                    for status in DependencyStatus
                ],
            )
            .group_by(DependencyResult.run_id)
            .subquery()
        )
        query = (
            select(
                RepoModel.id.label("project_id"),
                RepoModel.name.label("project_name"),
                func.coalesce(dependencies.c.count, 0).label("dependencies_count"),
                latest.id.label("run_id"),
                latest.state,
                latest.started_at,
                latest.finished_at,
                latest.error,
                snapshot.id.label("snapshot_id"),
                snapshot.finished_at.label("snapshot_at"),
                *[
                    func.coalesce(counts.c[status.value], 0).label(status.value)
                    for status in DependencyStatus
                ],
            )
            .select_from(RepoModel)
            .outerjoin(latest, latest.id == latest_id)
            .outerjoin(snapshot, snapshot.id == snapshot_id)
            .outerjoin(dependencies, dependencies.c.repo_id == RepoModel.id)
            .outerjoin(counts, counts.c.run_id == snapshot.id)
            .where(RepoModel.deleted_at.is_(None))
            .order_by(RepoModel.name, RepoModel.id)
        )
        try:
            rows = (await self.session.execute(query)).mappings().all()
        except SQLAlchemyError as error:
            raise StatusUnavailable("Не удалось получить статус") from error
        return [
            ProjectStatusDTO(
                project_id=row["project_id"],
                project_name=row["project_name"],
                dependencies_count=row["dependencies_count"],
                latest_run=CheckSummaryDTO(
                    run_id=row["run_id"],
                    state=RunState(row["state"]),
                    started_at=as_utc(row["started_at"]),
                    finished_at=as_utc(row["finished_at"]),
                    error=row["error"],
                )
                if row["run_id"] is not None
                else None,
                latest_snapshot_at=as_utc(row["snapshot_at"]),
                status_counts={status: row[status.value] for status in DependencyStatus}
                if row["snapshot_id"] is not None
                else None,
            )
            for row in rows
        ]
