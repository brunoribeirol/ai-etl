"""saved_pipelines.require_approval / approval_threshold_rows / last_approved_at
(ADR-028, Sprint 27)

Opt-in write-approval gate for scheduled pipelines: `require_approval` defaults
`false` (server_default), so every existing and new pipeline behaves exactly as
before until an operator explicitly turns this on. `approval_threshold_rows`
(nullable — no threshold means "always require approval" once the gate is on)
and `last_approved_at` (nullable — `NULL` means "never approved yet", the first
write is always gated regardless of the threshold) have no server default: both
are meaningless until `require_approval` is set, and every pre-existing row is
correctly represented by `NULL` for both. See `agents/loader.py::_is_write_gated`
for the full gate decision this backs.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "saved_pipelines",
        sa.Column("require_approval", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "saved_pipelines",
        sa.Column("approval_threshold_rows", sa.Integer(), nullable=True),
    )
    op.add_column(
        "saved_pipelines",
        sa.Column("last_approved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("saved_pipelines", "last_approved_at")
    op.drop_column("saved_pipelines", "approval_threshold_rows")
    op.drop_column("saved_pipelines", "require_approval")
