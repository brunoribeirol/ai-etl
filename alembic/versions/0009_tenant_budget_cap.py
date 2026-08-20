"""users.monthly_budget_usd — per-tenant budget cap (ADR-019, Sprint 29)

Purely additive: one new nullable column on `users`, no changes to
`runs`/`analysis_runs`/`stage_latencies`/`saved_pipelines`. Nullable because
a cap is opt-in — every existing tenant keeps today's uncapped behavior
until they (or an operator) sets one explicitly.

**Renumbered from `0007`/ADR-017 (merge-order collision).** Sprint 17
(`0007_run_pipeline_linkage.py`, ADR-017) and Sprint 14 (`0008_drift_threshold_pct.py`,
ADR-018, `down_revision = "0007"`) were built in parallel with this sprint and
all independently claimed the numbers this sprint originally used. Merge
order was set to 17 → 14 → 29, so this migration was renumbered to `0009`
with `down_revision = "0008"` at merge-conflict-resolution time — no schema
content changed, since this migration only ever touched `users` and neither
Sprint 17 (`runs`/`analysis_runs`) nor Sprint 14 (`saved_pipelines`, an index
on `runs`) touches that table. Verified locally by temporarily staging
copies of both `0007_run_pipeline_linkage.py` and `0008_drift_threshold_pct.py`
next to this file, running `alembic upgrade head` end-to-end (0001→0009),
then removing the copies again — same trick Sprint 14 used to verify against
Sprint 17 before this file existed.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-20
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("monthly_budget_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "monthly_budget_usd")
