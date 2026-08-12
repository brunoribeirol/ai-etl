"""add session scoping — tenant_id on runs and analysis_runs

Sprint A stopgap for the cross-session history leak in `app.py`'s "Histórico"
tab: `load_history()` had no filter at all, so any browser session could see
and download any other session's runs. `tenant_id` here holds a per-browser-
session UUID (`app.py::_get_session_id`), not a real tenant/account — that's
still future work. Nullable because existing rows predate this column and
there is nothing meaningful to backfill them with; a later migration will
introduce real tenancy and can decide what to do with pre-existing NULLs.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("tenant_id", sa.String(), nullable=True))
    op.add_column("analysis_runs", sa.Column("tenant_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("analysis_runs", "tenant_id")
    op.drop_column("runs", "tenant_id")
