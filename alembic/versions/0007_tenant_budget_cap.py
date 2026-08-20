"""users.monthly_budget_usd — per-tenant budget cap (ADR-017, Sprint 29)

Purely additive: one new nullable column on `users`, no changes to
`runs`/`analysis_runs`/`stage_latencies`/`saved_pipelines`. Nullable because
a cap is opt-in — every existing tenant keeps today's uncapped behavior
until they (or an operator) sets one explicitly.

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
    op.add_column("users", sa.Column("monthly_budget_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "monthly_budget_usd")
