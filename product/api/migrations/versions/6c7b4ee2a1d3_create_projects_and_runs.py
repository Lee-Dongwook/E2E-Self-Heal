"""create projects and runs

Revision ID: 6c7b4ee2a1d3
Revises: d7b220f72fba
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "6c7b4ee2a1d3"
down_revision: str | Sequence[str] | None = "d7b220f72fba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the persisted CI result model."""
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_repo_id"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("is_success", sa.Boolean(), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "workflow_run_id",
            "run_attempt",
            "kind",
            name="uq_runs_workflow_attempt_kind",
        ),
    )
    op.create_index("ix_runs_project_id", "runs", ["project_id"], unique=False)


def downgrade() -> None:
    """Drop the persisted CI result model."""
    op.drop_index("ix_runs_project_id", table_name="runs")
    op.drop_table("runs")
    op.drop_table("projects")
