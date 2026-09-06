"""admin_action_log.reason (ADR-047, MVP monetization-readiness plan WS3)

Adds a `reason` column to `admin_action_log` — the caller's stated
justification for a platform-admin action, required at the API layer
(`api/routers/admin.py`) for every route from this migration forward, but
nullable at the DB level: every row written before this migration has none,
and backfilling a fabricated reason for those would be worse than an honest
`NULL`. Same "optional at DB, enforced at API" shape as
`saved_pipelines.approval_threshold_rows` (ADR-028).

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-06

Chain note: originally authored as `0021` before finding a real fork already
on `main` — `0021_rls_tenant_isolation.py` (ADR-040) had already claimed that
number, both chaining onto `0020`. Renumbered to `0022`, chained onto the
real `0021`, same "keep the work, fix the pointer" convention already used
for the `0018`/`0019` fork (see that migration's own history).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("admin_action_log", sa.Column("reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("admin_action_log", "reason")
