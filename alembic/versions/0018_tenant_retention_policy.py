"""users.retention_days + retention_cleanup_log table (ADR-035, Sprint 36)

Two purely additive changes, same migration since both are Sprint 36's one
feature (configurable per-tenant retention):

1. `users.retention_days` — one new nullable column, same shape as `0009`'s
   `monthly_budget_usd`. Nullable because retention is opt-in — every
   existing tenant keeps today's "keep forever" behavior until they (or a
   future operator UI) set a window explicitly.
2. `retention_cleanup_log` — new, standalone table recording evidence that
   an automatic retention cleanup pass ran for a tenant (which storage keys
   were purged, and when), same "survives what it describes" shape as
   `0014`'s `tenant_deletion_log`.

RLS is enabled on the new table in this same migration per this project's
own standing rule (`SECURITY.md` — "Every new migration that adds a table
must enable RLS on it in the same migration"); this app's connection role
has `rolbypassrls = true`, so this is a safe no-op for application behavior
and only closes the anon/authenticated-default-CRUD hole Supabase opens on
every new table by default.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("retention_days", sa.Integer(), nullable=True))

    op.create_table(
        "retention_cleanup_log",
        sa.Column("id", sa.String(), primary_key=True),
        # Not a FK to users.id — same reasoning as tenant_deletion_log
        # (0014): a tenant that later deletes its own account (ADR-025) must
        # not cascade-delete this compliance evidence too, even though
        # retention cleanup itself (unlike account deletion) never removes
        # the users row.
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runs_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_keys_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_retention_cleanup_log_tenant", "retention_cleanup_log", ["tenant_id"])
    op.execute("ALTER TABLE retention_cleanup_log ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_retention_cleanup_log_tenant", table_name="retention_cleanup_log")
    op.drop_table("retention_cleanup_log")
    op.drop_column("users", "retention_days")
