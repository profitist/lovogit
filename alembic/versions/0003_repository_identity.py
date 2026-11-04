"""Стабильная GitHub-идентичность проектов."""

import sqlalchemy as sa

from alembic import op

revision = "0003_repository_identity"
down_revision = "0002_monitoring_state"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("repos", sa.Column("github_id", sa.Integer(), nullable=True))
    op.create_index("uq_repo_github_id", "repos", ["github_id"], unique=True)


def downgrade():
    op.drop_index("uq_repo_github_id", table_name="repos")
    op.drop_column("repos", "github_id")
