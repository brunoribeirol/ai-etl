"""users.locale (ADR-036, Sprint 25)

Per-tenant locale config — same shape as `users.retention_days`/`monthly_budget_usd`
(ADR-019/ADR-035): a single column, no history table. `NOT NULL DEFAULT 'pt-BR'` (not
nullable "no override" like `saved_pipelines.llm_provider`, ADR-031) — locale always has a
value, and every existing tenant reads back `'pt-BR'` after this migration, which is the
exact behavior every one of them already has today. Only `'pt-BR'`/`'en-US'` are valid
values, enforced by `core/locale.py::resolve_locale()` and the `PATCH /tenant/locale`
API boundary — never re-validated at read time (see ADR-036 §1).

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("locale", sa.String(length=10), nullable=False, server_default="pt-BR"),
    )


def downgrade() -> None:
    op.drop_column("users", "locale")
