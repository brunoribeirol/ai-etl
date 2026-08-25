"""Shared SQL-identifier and SQL-query safety checks (Sprint 33, Wave 0 2026-08-24).

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

    Uses `re.fullmatch` (not `re.match` with a trailing `$`) — `$` alone
    matches immediately before a trailing `\\n`, which would let
    `"users\\n"` slip through a `re.match(..., "^...$")` check. Cosmetic-only
    under the current call sites (no caller passes a literal newline today),
    flagged by Red Team's 2026-08-24 audit as defense-in-depth worth closing.
    """
    if allow_dots:
        if not _SAFE_TABLE_WITH_DOTS_RE.fullmatch(table):
            raise ValueError(
                f"Invalid table name '{table}': only alphanumeric, underscores, and dots allowed."
            )
    else:
        if not _SAFE_TABLE_NO_DOTS_RE.fullmatch(table):
            raise ValueError(
                f"Invalid table name '{table}': only alphanumeric and underscores allowed."
            )


# Wave 0 (2026-08-24 audit, Red Team CRITICAL finding): `sqlite_source.py::load_sqlite`
# and `mysql_source.py::load_mysql` accept an optional raw `query` string from
# `pipeline_plan` (LLM-generated) instead of the default `SELECT * FROM {validated_table}`.
# Unlike `table`, there is no SQLAlchemy bind-parameter syntax for an entire arbitrary
# query, so this is the injection defense for that path — confirmed exploited for real:
# `{"type": "sqlite", "query": "DROP TABLE users; --"}` was passed straight to
# `pd.read_sql(text(sql), conn)` with zero validation and actually dropped the table.
# Mirrors `sources/mongodb_source.py::_validate_query`'s connector-level denylist
# pattern — runs inside the connector itself, regardless of caller.
_SELECT_PREFIX_RE = re.compile(r"^\s*select\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")

_FORBIDDEN_QUERY_KEYWORDS = frozenset(
    {
        "drop",
        "delete",
        "insert",
        "update",
        "alter",
        "truncate",
        "create",
        "replace",
        "grant",
        "revoke",
        "attach",
        "detach",
        "pragma",
        "exec",
        "execute",
        "call",
        "merge",
        "vacuum",
        "outfile",
        "load_file",
    }
)


def validate_select_only_query(sql: str) -> None:
    """Raise `ValueError` unless `sql` is a single, read-only `SELECT` statement.

    Rejects: anything not starting with `SELECT`; a `;` anywhere but a single
    optional trailing one (blocks statement stacking, e.g. `SELECT 1; DROP
    TABLE users`); `--`/`/*` comment markers (can hide a stacked statement
    from a naive prefix check); any forbidden keyword appearing as a whole
    word anywhere in the string (catches DDL/DML regardless of where in the
    string it appears, not just at the start).
    """
    stripped = sql.strip()
    if not _SELECT_PREFIX_RE.match(stripped):
        raise ValueError("Only SELECT queries are allowed in a source's custom `query`.")

    body = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in body:
        raise ValueError("Multiple SQL statements are not allowed in a source's custom `query`.")

    if "--" in stripped or "/*" in stripped:
        raise ValueError("SQL comments are not allowed in a source's custom `query`.")

    words = {w.lower() for w in _WORD_RE.findall(stripped)}
    forbidden = words & _FORBIDDEN_QUERY_KEYWORDS
    if forbidden:
        raise ValueError(f"Query contains forbidden keyword(s): {sorted(forbidden)}")
