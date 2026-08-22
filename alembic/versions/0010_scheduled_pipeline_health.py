"""saved_pipelines health-tracking columns (ADR-020, Sprint 15)

Purely additive: three new columns on `saved_pipelines`, no changes to any
other table. `consecutive_failures` backfills every existing row to 0 (its
`server_default`) — zero behavior change for a pipeline that has never had
a health snapshot computed for it before this migration.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-21
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "saved_pipelines",
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("saved_pipelines", sa.Column("last_status", sa.String(length=20), nullable=True))
    op.add_column("saved_pipelines", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("saved_pipelines", "last_error")
    op.drop_column("saved_pipelines", "last_status")
    op.drop_column("saved_pipelines", "consecutive_failures")
