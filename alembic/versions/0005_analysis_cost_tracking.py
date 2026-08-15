"""analysis_runs cost tracking — model_name + cost_usd (ADR-008, Sprint 3)

Purely additive: two new nullable columns on `analysis_runs`, no changes to
`runs`/`stage_latencies`/`users`. Nullable because historical rows predate
cost tracking and have no model_name/cost_usd to backfill (see
core/pricing.py's compute_cost_usd docstring for why cost_usd is nullable
going forward too, not just for pre-existing rows).

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-15
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("model_name", sa.String(length=50), nullable=True))
    op.add_column("analysis_runs", sa.Column("cost_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("analysis_runs", "cost_usd")
    op.drop_column("analysis_runs", "model_name")
