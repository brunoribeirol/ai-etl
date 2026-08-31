"""Unit tests for the MongoDB source connector (Sprint 11, ADR-012).

Only the network transport (`pymongo.MongoClient`) is mocked — same
convention already used for `rest_source.py`'s `httpx.get` — so the real
schema-inference logic (`pd.json_normalize` unioning heterogeneous document
shapes, `_id` stringification) and the query-validation guard run for real.
Live-MongoDB coverage lives in `tests/integration/test_mongodb_source_real.py`
(self-skips without `make mongodb-test-up`).
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from ai_etl.core.tenant_context import tenant_connections
from ai_etl.sources.mongodb_source import DEFAULT_SAMPLE_LIMIT, _validate_query, load_mongodb


def _mock_client_from(mongo_client_mock: MagicMock, documents: list[dict]) -> MagicMock:
    """Configure an already-patched `MongoClient` mock's return value — split
    out of `_mock_client` (ADR-044) so a test can hold onto the constructor
    mock itself (e.g. to assert which connection string it was called with),
    not just the collection it ultimately returns.
    """
    mock_cursor = MagicMock()
    mock_cursor.limit.return_value = documents

    mock_collection = MagicMock()
    mock_collection.find.return_value = mock_cursor

    mock_db = MagicMock()
    mock_db.__getitem__.return_value = mock_collection

    mock_client_instance = MagicMock()
    mock_client_instance.__getitem__.return_value = mock_db
    mock_client_instance.__enter__.return_value = mock_client_instance
    mock_client_instance.__exit__.return_value = False

    mongo_client_mock.return_value = mock_client_instance
    return mock_collection


def _mock_client(mocker, documents: list[dict]) -> MagicMock:
    mongo_client_mock = mocker.patch("ai_etl.sources.mongodb_source.MongoClient")
    return _mock_client_from(mongo_client_mock, documents)


# --- _validate_query() ---


def test_validate_query_allows_plain_filter() -> None:
    _validate_query({"status": "active"})  # must not raise


@pytest.mark.parametrize("operator", ["$where", "$function", "$accumulator"])
def test_validate_query_rejects_js_operators(operator: str) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        _validate_query({operator: "return true"})


# --- load_mongodb() ---


def test_load_mongodb_missing_uri_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONGODB_URI", raising=False)
    with pytest.raises(EnvironmentError, match="MONGODB_URI"):
        load_mongodb("db", "orders")


def test_load_mongodb_uses_tenant_override_over_shared_env(
    mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ADR-044: a tenant's own stored connection string must win over the
    # deployment-wide MONGODB_URI, even when the latter is set.
    monkeypatch.setenv("MONGODB_URI", "mongodb://shared-host:27017")
    mongo_client_mock = mocker.patch("ai_etl.sources.mongodb_source.MongoClient")
    _mock_client_from(mongo_client_mock, [{"_id": "abc123", "product": "A"}])

    with tenant_connections({"mongodb": "mongodb://tenant-host:27017"}):
        load_mongodb("shop", "orders")

    mongo_client_mock.assert_called_once_with("mongodb://tenant-host:27017")


def test_load_mongodb_falls_back_to_shared_env_with_no_override(
    mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://shared-host:27017")
    mongo_client_mock = mocker.patch("ai_etl.sources.mongodb_source.MongoClient")
    _mock_client_from(mongo_client_mock, [{"_id": "abc123", "product": "A"}])

    load_mongodb("shop", "orders")

    mongo_client_mock.assert_called_once_with("mongodb://shared-host:27017")


def test_load_mongodb_reads_documents(mocker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    documents = [{"_id": "abc123", "product": "A"}, {"_id": "def456", "product": "B"}]
    _mock_client(mocker, documents)

    df = load_mongodb("shop", "orders")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert set(df["product"]) == {"A", "B"}


def test_load_mongodb_stringifies_object_id(mocker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")

    class _FakeObjectId:
        def __str__(self) -> str:
            return "507f1f77bcf86cd799439011"

    documents = [{"_id": _FakeObjectId(), "product": "A"}]
    _mock_client(mocker, documents)

    df = load_mongodb("shop", "orders")

    assert df["_id"].iloc[0] == "507f1f77bcf86cd799439011"
    assert isinstance(df["_id"].iloc[0], str)


def test_load_mongodb_heterogeneous_documents_union_columns(
    mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    documents = [{"_id": "1", "product": "A", "tags": ["x"]}, {"_id": "2", "product": "B"}]
    _mock_client(mocker, documents)

    df = load_mongodb("shop", "orders")

    assert "tags" in df.columns
    assert df.loc[df["product"] == "B", "tags"].isna().all()


def test_load_mongodb_passes_query_and_limit(mocker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    mock_collection = _mock_client(mocker, [{"_id": "1", "product": "A"}])

    load_mongodb("shop", "orders", query={"product": "A"}, limit=5)

    mock_collection.find.assert_called_once_with({"product": "A"})
    mock_collection.find.return_value.limit.assert_called_once_with(5)


def test_load_mongodb_default_limit_applied(mocker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    mock_collection = _mock_client(mocker, [])

    load_mongodb("shop", "orders")

    mock_collection.find.return_value.limit.assert_called_once_with(DEFAULT_SAMPLE_LIMIT)


def test_load_mongodb_rejects_forbidden_query_operator(
    mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    mocker.patch("ai_etl.sources.mongodb_source.MongoClient")

    with pytest.raises(ValueError, match="forbidden"):
        load_mongodb("shop", "orders", query={"$where": "this.a == this.b"})
