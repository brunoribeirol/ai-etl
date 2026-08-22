"""saved_pipelines.quality_rules (ADR-023, Sprint 16)

Additive, nullable-safe (server_default '[]') JSON column — operator-defined data
quality rules for a saved pipeline, folded into that pipeline's subsequent
`quality_report`s alongside the fixed checks (`agents/quality.py`). Backfills every
existing row to an empty rule set, zero behavior change until an operator adds a rule.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-21
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "saved_pipelines",
        sa.Column("quality_rules", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("saved_pipelines", "quality_rules")
