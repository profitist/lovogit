"""История проверок и снимки результатов."""

import sqlalchemy as sa

from alembic import op

revision = "0002_monitoring_state"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "monitoring_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("repo_id", sa.Integer, sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.CheckConstraint(
            "state IN ('running','succeeded','failed')", name="ck_run_state"
        ),
        sa.CheckConstraint(
            "(state = 'running' AND finished_at IS NULL AND error IS NULL) OR "
            "(state = 'succeeded' AND finished_at IS NOT NULL AND error IS NULL) OR "
            "(state = 'failed' AND finished_at IS NOT NULL AND error IS NOT NULL "
            "AND length(trim(error)) > 0)",
            name="ck_run_completion",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="ck_run_time"
        ),
    )
    op.create_index(
        "ix_run_repo_started", "monitoring_runs", ["repo_id", "started_at", "id"]
    )
    op.create_table(
        "dependency_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "run_id", sa.Integer, sa.ForeignKey("monitoring_runs.id"), nullable=False
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("current_version", sa.String(255), nullable=False),
        sa.Column("latest_version", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.UniqueConstraint("run_id", "name", name="uq_result_run_name"),
        sa.CheckConstraint(
            "status IN ('incompat','review','failed','waiting','low','current')",
            name="ck_result_status",
        ),
    )


def downgrade():
    op.drop_table("dependency_results")
    op.drop_index("ix_run_repo_started", table_name="monitoring_runs")
    op.drop_table("monitoring_runs")
