"""Unit tests for `core.sql_safety` — table-name and custom-query validation.

`validate_select_only_query` is the Wave 0 fix (2026-08-24 audit, Red Team CRITICAL
finding): `{"type": "sqlite", "query": "DROP TABLE users; --"}` was passed straight to
`pd.read_sql` with zero validation and actually dropped a real table. These tests
reproduce that exact payload alongside other stacking/comment/keyword variants.
"""

import pytest

from ai_etl.core.sql_safety import validate_select_only_query, validate_table_name


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders",
        "select id, product from orders where revenue > 150",
        "SELECT * FROM orders;",
        "  SELECT * FROM orders  ",
    ],
)
def test_valid_select_queries_pass(sql: str) -> None:
    validate_select_only_query(sql)  # must not raise


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE users; --",  # the exact Red Team payload
        "SELECT * FROM orders; DROP TABLE orders; --",
        "UPDATE orders SET revenue = 0",
        "DELETE FROM orders",
        "INSERT INTO orders VALUES (1, 'x', 1.0)",
        "ALTER TABLE orders ADD COLUMN hacked TEXT",
        "SELECT * FROM orders -- ; DROP TABLE orders",
        "SELECT * FROM orders /* comment */",
        "PRAGMA writable_schema = 1",
        "ATTACH DATABASE '/etc/passwd' AS pwned",
        "SELECT load_file('/etc/passwd')",
        "SELECT 1; SELECT 2",
        "not even sql",
    ],
)
def test_dangerous_queries_raise(sql: str) -> None:
    with pytest.raises(ValueError):
        validate_select_only_query(sql)


@pytest.mark.parametrize("name", ["orders", "_private_table", "orders2"])
def test_valid_table_names_pass(name: str) -> None:
    validate_table_name(name, allow_dots=False)


def test_trailing_newline_no_longer_bypasses_check() -> None:
    # Red Team, 2026-08-24 audit: `re.match(..., "^...$")` lets `"$"` match right
    # before a trailing "\n" — cosmetic under current call sites, but a real gap in
    # the check itself. `fullmatch` closes it.
    with pytest.raises(ValueError, match="Invalid table name"):
        validate_table_name("orders\n", allow_dots=False)
    with pytest.raises(ValueError, match="Invalid table name"):
        validate_table_name("orders\n", allow_dots=True)
