"""stage_latencies table — per-stage latency instrumentation (ADR-007)

Purely additive: a new table, no changes to `runs`/`analysis_runs`. See
ADR-007 for why a narrow long-format table (with a `seq` counter) was chosen
over new columns — the Silver pipeline always runs the same 5 LangGraph nodes
exactly once, but one `analysis_runs` row can correspond to multiple Analyst/
Science sub-task calls plus repair reruns, which a fixed column set can't
represent without an array column or repeated NULL-padded columns.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-14
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stage_latencies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("run_type", sa.String(length=10), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("timed_out", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["users.id"], name="fk_stage_latencies_tenant_id_users"
        ),
    )
    op.create_index("ix_stage_latencies_tenant_stage", "stage_latencies", ["tenant_id", "stage"])


def downgrade() -> None:
    op.drop_index("ix_stage_latencies_tenant_stage", table_name="stage_latencies")
    op.drop_table("stage_latencies")
