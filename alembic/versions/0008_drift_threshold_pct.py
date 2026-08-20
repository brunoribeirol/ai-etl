"""saved_pipelines.drift_threshold_pct + a composite index on runs (ADR-018,
Sprint 14)

**Reconciled against Sprint 17 (`0007_run_pipeline_linkage.py`, ADR-017),
which merges first.** Both sprints independently added `runs.saved_pipeline_id`
in parallel; Sprint 17's version is the more complete one (adds the same FK
to `analysis_runs` too, `ON DELETE SET NULL` on both) and is the one that
actually creates the column — this migration no longer does (see the
original `0007_drift_alerts.py` this replaced, kept out of git history
cleanly via this rename+rewrite rather than a second column-creation
attempt that would fail with "column already exists" once both migrations
run against the same database).

This migration is genuinely only two things:
1. `saved_pipelines.drift_threshold_pct` — Sprint 17 never touches
   `saved_pipelines`, so this is fully additive and non-overlapping.
2. A composite index `(saved_pipeline_id, timestamp)` on `runs` — Sprint
   17's own migration only adds a single-column index on
   `saved_pipeline_id`. `audit.db.get_previous_completed_run`'s query
   (`WHERE saved_pipeline_id = :id ... ORDER BY timestamp DESC LIMIT 1`)
   benefits from the composite form: Postgres can satisfy the whole
   predicate + ordering from one index scan (walking the index backwards
   for the `LIMIT 1`) instead of filtering via the single-column index and
   then sorting the matches separately. Kept as a genuinely additional index
   alongside Sprint 17's, not a replacement for it — declaring it here
   (rather than folding it into Sprint 17's migration) keeps this sprint's
   own, purely-additive concern out of a migration it doesn't own.

`down_revision` points at Sprint 17's `"0007"` — this file cannot apply on
its own until that migration exists in the same `alembic/versions/`
directory (i.e., after Sprint 17 merges to `main` and this branch is rebased
on top of it, or after both merge in that order). Verified locally by
temporarily staging a copy of Sprint 17's `0007_run_pipeline_linkage.py`
next to this file, running `alembic upgrade head` end-to-end, then removing
the copy again — see the PR description for the real-Postgres verification
this produced. Not committed here: that file is Sprint 17's to add.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-20
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "saved_pipelines",
        sa.Column(
            "drift_threshold_pct",
            sa.Float(),
            nullable=False,
            server_default="20.0",
        ),
    )
    # `runs.saved_pipeline_id` itself already exists — created by Sprint 17's
    # 0007 migration, which this one revises. Only the composite index is
    # this migration's to add; see the module docstring for why it's kept
    # alongside (not instead of) Sprint 17's own single-column index.
    op.create_index(
        "ix_runs_saved_pipeline_timestamp",
        "runs",
        ["saved_pipeline_id", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_runs_saved_pipeline_timestamp", table_name="runs")
    op.drop_column("saved_pipelines", "drift_threshold_pct")
