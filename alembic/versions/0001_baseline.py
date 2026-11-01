"""Исходные таблицы проектов и зависимостей."""

import sqlalchemy as sa

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "repos",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("repo_url", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    op.create_table(
        "dependencies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("repo_id", sa.Integer, sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("current_version", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )


def downgrade():
    op.drop_table("dependencies")
    op.drop_table("repos")
