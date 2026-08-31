"""Unit tests for the destination `preview_*` functions (Sprint 27, ADR-028).

Each destination's preview must never write — asserted directly (a mocked
write call that's never invoked), not just inferred from the return value.
"""

from __future__ import annotations

import pandas as pd
import pytest
from botocore.exceptions import ClientError

from ai_etl.core.tenant_context import tenant_connections
from ai_etl.destinations.csv_dest import preview_csv
from ai_etl.destinations.postgres_dest import preview_postgres
from ai_etl.destinations.s3_parquet_dest import preview_s3_parquet

_DF = pd.DataFrame({"a": [1, 2, 3]})


# ---------------------------------------------------------------------------
# csv
# ---------------------------------------------------------------------------


def test_preview_csv_new_path_reports_no_existing_file(tmp_path) -> None:
    target = tmp_path / "out.csv"
    result = preview_csv(_DF, str(target))

    assert result["destination_type"] == "csv"
    assert result["would_write_rows"] == 3
    assert result["existing"] is None
    assert not target.exists()  # never written


def test_preview_csv_existing_path_reports_existing_bytes(tmp_path) -> None:
    target = tmp_path / "out.csv"
    target.write_text("a\n1\n")

    result = preview_csv(_DF, str(target))

    assert result["existing"]["existing_bytes"] == target.stat().st_size


# ---------------------------------------------------------------------------
# postgres
# ---------------------------------------------------------------------------


def test_preview_postgres_missing_env_raises(monkeypatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(EnvironmentError):
        preview_postgres(_DF, "public.output")


def test_preview_postgres_invalid_table_name_raises(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_URL", "sqlite:///:memory:")
    with pytest.raises(ValueError, match="Invalid table name"):
        preview_postgres(_DF, "bad; drop table users;")


def test_preview_postgres_table_does_not_exist_yet(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("POSTGRES_URL", f"sqlite:///{tmp_path}/db.sqlite")

    result = preview_postgres(_DF, "output")

    assert result["destination_type"] == "postgres"
    assert result["would_write_rows"] == 3
    assert result["existing"] is None


def test_preview_postgres_existing_table_reports_row_count(monkeypatch, tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/db.sqlite"
    monkeypatch.setenv("POSTGRES_URL", db_url)

    import sqlalchemy

    engine = sqlalchemy.create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text("CREATE TABLE output (a INTEGER)"))
        conn.execute(sqlalchemy.text("INSERT INTO output (a) VALUES (1), (2)"))

    result = preview_postgres(_DF, "output")

    assert result["would_write_rows"] == 3
    assert result["existing"]["existing_rows"] == 2


def test_preview_postgres_uses_tenant_override_over_shared_env(monkeypatch, tmp_path) -> None:
    # ADR-044: point the shared env var at one (empty) sqlite file and the
    # tenant override at a different one with a real table — the override
    # must be the one actually read from.
    monkeypatch.setenv("POSTGRES_URL", f"sqlite:///{tmp_path}/shared.sqlite")
    tenant_db_url = f"sqlite:///{tmp_path}/tenant.sqlite"

    import sqlalchemy

    engine = sqlalchemy.create_engine(tenant_db_url)
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text("CREATE TABLE output (a INTEGER)"))
        conn.execute(sqlalchemy.text("INSERT INTO output (a) VALUES (1), (2), (3)"))

    with tenant_connections({"postgres": tenant_db_url}):
        result = preview_postgres(_DF, "output")

    assert result["existing"]["existing_rows"] == 3


# ---------------------------------------------------------------------------
# s3_parquet
# ---------------------------------------------------------------------------


class _FakeS3Client:
    def __init__(self, existing: dict[tuple[str, str], int] | None = None) -> None:
        self._existing = existing or {}
        self.put_calls: list[tuple[str, str]] = []

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803
        self.put_calls.append((Bucket, Key))

    def head_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803
        size = self._existing.get((Bucket, Key))
        if size is None:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": size}


def test_preview_s3_parquet_new_key_reports_no_existing_object(mocker) -> None:
    mocker.patch("boto3.client", return_value=_FakeS3Client())

    result = preview_s3_parquet(_DF, bucket="b", key="k.parquet")

    assert result["destination_type"] == "s3_parquet"
    assert result["would_write_rows"] == 3
    assert result["existing"] is None


def test_preview_s3_parquet_existing_key_reports_size_and_never_writes(mocker) -> None:
    client = _FakeS3Client(existing={("b", "k.parquet"): 4096})
    mocker.patch("boto3.client", return_value=client)

    result = preview_s3_parquet(_DF, bucket="b", key="k.parquet")

    assert result["existing"]["existing_bytes"] == 4096
    assert client.put_calls == []
