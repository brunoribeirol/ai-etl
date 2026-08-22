"""Shared SQL-identifier safety checks (Sprint 33).

Extracted from `sources/postgres_source.py`, `sources/mysql_source.py`,
`sources/sqlite_source.py`, and `destinations/postgres_dest.py`, which each
duplicated an identical (or near-identical) `_validate_table_name()` helper.
This is the single source of truth going forward — pure refactor, no change
in validation behavior for any of the four call sites.

Table names are validated against a safe-identifier allowlist before being
interpolated into a `SELECT * FROM {table}`-style string (SQLAlchemy has no
bind-parameter syntax for identifiers, only values, so this allowlist check
is the injection defense — never an f-string with unvalidated input).

**Divergence found during extraction (Sprint 33):** `sqlite_source.py` used a
stricter regex than the other three modules — no `.` allowed, since SQLite
has no `schema.table` notion the way Postgres/MySQL do (documented in its
original docstring). This isn't an accidental drift between copies; it's a
deliberate per-engine difference. Rather than pick one regex and silently
change behavior for some callers, `validate_table_name()` takes an explicit
`allow_dots` flag so each call site keeps its exact prior behavior. Default
is `allow_dots=False` (the more restrictive option) so a future call site
that forgets to pass the flag fails safe.
"""

import re

_SAFE_TABLE_WITH_DOTS_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")
_SAFE_TABLE_NO_DOTS_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def validate_table_name(table: str, *, allow_dots: bool = False) -> None:
    """Raise `ValueError` if `table` is not a safe SQL identifier.

    `allow_dots=True` permits `schema.table` form (Postgres, MySQL/MariaDB).
    `allow_dots=False` (default) restricts to a bare identifier (SQLite,
    which has no schema-qualification notion) — the more restrictive option,
    so omitting the flag fails safe.
    """
    if allow_dots:
        if not _SAFE_TABLE_WITH_DOTS_RE.match(table):
            raise ValueError(
                f"Invalid table name '{table}': only alphanumeric, underscores, and dots allowed."
            )
    else:
        if not _SAFE_TABLE_NO_DOTS_RE.match(table):
            raise ValueError(
                f"Invalid table name '{table}': only alphanumeric and underscores allowed."
            )
