from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .engine import Base


class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = (Index("uq_repo_github_id", "github_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    github_id: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))

    repo_url: Mapped[str] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    dependencies = relationship("RepoDependency", back_populates="repo")


class RepoDependency(Base):
    __tablename__ = "dependencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255))

    current_version: Mapped[str] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    repo: Mapped[Repo] = relationship("Repo", back_populates="dependencies")


class MonitoringRun(Base):
    __tablename__ = "monitoring_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('running','succeeded','failed')", name="ck_run_state"
        ),
        CheckConstraint(
            "(state = 'running' AND finished_at IS NULL AND error IS NULL) OR "
            "(state = 'succeeded' AND finished_at IS NOT NULL AND error IS NULL) OR "
            "(state = 'failed' AND finished_at IS NOT NULL AND error IS NOT NULL "
            "AND length(trim(error)) > 0)",
            name="ck_run_completion",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="ck_run_time"
        ),
        Index("ix_run_repo_started", "repo_id", "started_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    state: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)


class DependencyResult(Base):
    __tablename__ = "dependency_results"
    __table_args__ = (
        UniqueConstraint("run_id", "name", name="uq_result_run_name"),
        CheckConstraint(
            "status IN ('incompat','review','failed','waiting','low','current')",
            name="ck_result_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("monitoring_runs.id"))
    name: Mapped[str] = mapped_column(String(255))
    current_version: Mapped[str] = mapped_column(String(255))
    latest_version: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))
