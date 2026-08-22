"""Unit tests for source/destination table name validation."""

import pytest

from ai_etl.core.sql_safety import validate_table_name


def src_validate(name: str) -> None:
    validate_table_name(name, allow_dots=True)


def dest_validate(name: str) -> None:
    validate_table_name(name, allow_dots=True)


@pytest.mark.parametrize("name", ["orders", "public.customers", "schema_1.table_2", "_private"])
def test_valid_table_names_pass(name: str) -> None:
    src_validate(name)  # must not raise
    dest_validate(name)  # must not raise


@pytest.mark.parametrize(
    "name",
    [
        "orders; DROP TABLE orders; --",
        "public.orders; SELECT 1",
        "1_invalid_start",
        "",
        "table name with spaces",
        "table'injection",
    ],
)
def test_invalid_table_names_raise(name: str) -> None:
    with pytest.raises(ValueError, match="Invalid table name"):
        src_validate(name)
    with pytest.raises(ValueError, match="Invalid table name"):
        dest_validate(name)
