"""saved_pipelines table (ADR-016, Sprint 13)

Adds the new `saved_pipelines` table only — no change to any existing table.
A saved pipeline is a persisted spec + cron schedule + tenant, reexecuted
unattended by Celery beat (services/scheduler.py); `runs`/`analysis_runs`
are untouched, since a scheduled execution still produces a normal `runs`
row via the same `run_full_analysis_task` an avulso run uses.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-19
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_pipelines",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column("business_question", sa.Text(), nullable=False, server_default=""),
        sa.Column("cron_schedule", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_task_id", sa.String(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_saved_pipelines_active_next_run",
        "saved_pipelines",
        ["is_active", "next_run_at"],
    )
    op.create_index("ix_saved_pipelines_tenant", "saved_pipelines", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_pipelines_tenant", table_name="saved_pipelines")
    op.drop_index("ix_saved_pipelines_active_next_run", table_name="saved_pipelines")
    op.drop_table("saved_pipelines")
