from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from lovogit.app.services.status import CheckSummaryDTO, as_utc
from lovogit.db import DependencyResult as DependencyResultModel
from lovogit.db import DependencyStatus, RepoModel, RunState
from lovogit.db import MonitoringRun as MonitoringRunModel


class AttentionUnavailable(Exception):
    """Не удалось прочитать список требующих внимания зависимостей."""


@dataclass(frozen=True)
class AttentionDependencyDTO:
    name: str
    current_version: str
    latest_version: str | None
    status: DependencyStatus


@dataclass(frozen=True)
class ProjectAttentionDTO:
    project_id: int
    project_name: str
    latest_run: CheckSummaryDTO | None
    snapshot_id: int | None
    latest_snapshot_at: datetime | None
    dependencies: tuple[AttentionDependencyDTO, ...]


class AttentionService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_overview(self) -> list[ProjectAttentionDTO]:
        latest = aliased(MonitoringRunModel)
        snapshot = aliased(MonitoringRunModel)
        latest_id = (
            select(MonitoringRunModel.id)
            .where(MonitoringRunModel.repo_id == RepoModel.id)
            .order_by(MonitoringRunModel.started_at.desc(), MonitoringRunModel.id.desc())
            .limit(1)
            .correlate(RepoModel)
            .scalar_subquery()
        )
        snapshot_id = (
            select(MonitoringRunModel.id)
            .where(
                MonitoringRunModel.repo_id == RepoModel.id,
                MonitoringRunModel.state == RunState.SUCCEEDED,
            )
            .order_by(MonitoringRunModel.started_at.desc(), MonitoringRunModel.id.desc())
            .limit(1)
            .correlate(RepoModel)
            .scalar_subquery()
        )
        priority = {
            DependencyStatus.INCOMPAT: 0,
            DependencyStatus.REVIEW: 1,
            DependencyStatus.FAILED: 2,
        }
        query = (
            select(
                RepoModel.id.label("project_id"),
                RepoModel.name.label("project_name"),
                latest.id.label("run_id"),
                latest.state,
                latest.started_at,
                latest.finished_at,
                latest.error,
                snapshot.id.label("snapshot_id"),
                snapshot.finished_at.label("snapshot_at"),
                DependencyResultModel.name,
                DependencyResultModel.current_version,
                DependencyResultModel.latest_version,
                DependencyResultModel.status,
            )
            .select_from(RepoModel)
            .outerjoin(latest, latest.id == latest_id)
            .outerjoin(snapshot, snapshot.id == snapshot_id)
            .outerjoin(
                DependencyResultModel,
                (DependencyResultModel.run_id == snapshot.id)
                & DependencyResultModel.status.in_(priority),
            )
            .where(RepoModel.deleted_at.is_(None))
            .order_by(
                RepoModel.name,
                RepoModel.id,
                case(priority, value=DependencyResultModel.status, else_=3),
                DependencyResultModel.name,
            )
        )
        try:
            rows = (await self._session.execute(query)).mappings().all()
        except SQLAlchemyError as error:
            raise AttentionUnavailable(
                "Не удалось прочитать зависимости, требующие внимания, по проектам"
            ) from error

        projects = []
        dependencies = []
        for index, row in enumerate(rows):
            if row["name"] is not None:
                dependencies.append(
                    AttentionDependencyDTO(
                        name=row["name"],
                        current_version=row["current_version"],
                        latest_version=row["latest_version"],
                        status=DependencyStatus(row["status"]),
                    )
                )
            if (
                index + 1 < len(rows)
                and rows[index + 1]["project_id"] == row["project_id"]
            ):
                continue
            projects.append(
                ProjectAttentionDTO(
                    project_id=row["project_id"],
                    project_name=row["project_name"],
                    latest_run=CheckSummaryDTO(
                        run_id=row["run_id"],
                        state=RunState(row["state"]),
                        started_at=as_utc(row["started_at"]),
                        finished_at=as_utc(row["finished_at"]),
                        error=row["error"],
                    )
                    if row["run_id"] is not None
                    else None,
                    snapshot_id=row["snapshot_id"],
                    latest_snapshot_at=as_utc(row["snapshot_at"]),
                    dependencies=tuple(dependencies),
                )
            )
            dependencies = []
        return projects
