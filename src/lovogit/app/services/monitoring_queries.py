from sqlalchemy import select
from sqlalchemy.sql.selectable import ScalarSelect

from lovogit.db import MonitoringRun as MonitoringRunModel
from lovogit.db import RepoModel, RunState


def latest_run_id(*, state: RunState | None = None) -> ScalarSelect:
    query = select(MonitoringRunModel.id).where(
        MonitoringRunModel.repo_id == RepoModel.id
    )
    if state is not None:
        query = query.where(MonitoringRunModel.state == state)
    return (
        query.order_by(
            MonitoringRunModel.started_at.desc(), MonitoringRunModel.id.desc()
        )
        .limit(1)
        .correlate(RepoModel)
        .scalar_subquery()
    )
