"""saved_pipeline_id on runs/analysis_runs (ADR-017, Sprint 17)

Adds a nullable `saved_pipeline_id` FK column to both `runs` and
`analysis_runs`, so an execution produced by a scheduled saved pipeline
(Sprint 13, ADR-016) can be grouped with every other execution of that same
pipeline — the linkage Sprint 17's time-series/diff view needs and that
`saved_pipelines.last_task_id` alone cannot provide (it only remembers the
single most recent fire, not full history). `ON DELETE SET NULL`: deleting a
saved pipeline must not delete the runs it already produced — run history is
audit data (ADR-004's whole premise), independent of whether the pipeline
that created it still exists.

No backfill: every row that predates this column reads back NULL
(`saved_pipeline_id IS NULL` means "avulso, or scheduled before Sprint 17" —
indistinguishable, and that is an accepted, documented limitation, not a bug
— see ADR-017).

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-20
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("saved_pipeline_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_runs_saved_pipeline_id",
        "runs",
        "saved_pipelines",
        ["saved_pipeline_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_runs_saved_pipeline_id", "runs", ["saved_pipeline_id"])

    op.add_column("analysis_runs", sa.Column("saved_pipeline_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_analysis_runs_saved_pipeline_id",
        "analysis_runs",
        "saved_pipelines",
        ["saved_pipeline_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_analysis_runs_saved_pipeline_id", "analysis_runs", ["saved_pipeline_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_runs_saved_pipeline_id", table_name="analysis_runs")
    op.drop_constraint("fk_analysis_runs_saved_pipeline_id", "analysis_runs", type_="foreignkey")
    op.drop_column("analysis_runs", "saved_pipeline_id")

    op.drop_index("ix_runs_saved_pipeline_id", table_name="runs")
    op.drop_constraint("fk_runs_saved_pipeline_id", "runs", type_="foreignkey")
    op.drop_column("runs", "saved_pipeline_id")
