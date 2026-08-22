"""admin_action_log table (ADR-032, Sprint 31)

New, standalone table — no changes to any existing table. Records every
platform-admin cross-tenant action (Sprint 31's `admin` RBAC role, ADR-032
Decision 2): who (`actor_user_id`, the individual Clerk user id — platform
admins are identified via `AI_ETL_PLATFORM_ADMINS`, not a Clerk org role),
what (`action`), against which tenant (`target_tenant_id`, nullable — some
admin actions, e.g. listing all tenants, have no single target), and when.
Written by `src/ai_etl/audit/admin_log.py`, a module deliberately kept out
of `audit/db.py` (Sprint 31 isolation note: minimize merge conflict with
other sprints touching that file in the same parallel batch).

RLS is enabled in this same migration per this project's own standing rule
(`SECURITY.md` — "Every new migration that adds a table must enable RLS on
it in the same migration"); this app's connection role has
`rolbypassrls = true` (see ADR-032 Decision 1 for why that posture itself
is being kept, not changed, this sprint), so this is a safe no-op for
application behavior and only closes the anon/authenticated-default-CRUD
hole Supabase opens on every new table by default.

Revision ID: 0017
Revises: 0015

Chain-reconciliation note (same pattern as Sprints 14/17/29): `0016` was
pre-assigned to Sprint 30, running in parallel in a different worktree at
the time this migration was authored, and did not exist on this branch yet.
This migration therefore chains onto `0015` (the actual tip at authoring
time) rather than `0016`. Whichever of the two sprints' PRs merges second
must rebase its migration's `down_revision` onto the other's revision id
before merge, restoring a single linear chain — flagged here explicitly
rather than left as a silent gap, matching how the same situation was
resolved in Sprints 14/17/29.

Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_action_log",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_tenant_id", sa.String(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_action_log_actor", "admin_action_log", ["actor_user_id"])
    op.create_index("ix_admin_action_log_target", "admin_action_log", ["target_tenant_id"])
    op.create_index("ix_admin_action_log_created", "admin_action_log", ["created_at"])
    op.execute("ALTER TABLE admin_action_log ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_admin_action_log_created", table_name="admin_action_log")
    op.drop_index("ix_admin_action_log_target", table_name="admin_action_log")
    op.drop_index("ix_admin_action_log_actor", table_name="admin_action_log")
    op.drop_table("admin_action_log")
