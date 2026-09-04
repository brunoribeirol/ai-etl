"""Unit tests for `destinations/mongodb_dest.py::save_mongodb`/`preview_mongodb`.

Only the network transport (`pymongo.MongoClient`) is mocked — same
convention `tests/unit/test_mongodb_source.py` already established for the
source-side connector.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from ai_etl.core.tenant_context import tenant_connections
from ai_etl.destinations.mongodb_dest import preview_mongodb, save_mongodb

_DF = pd.DataFrame({"a": [1, 2, 3]})


def _mock_client(mocker, existing_count: int = 0) -> tuple[MagicMock, MagicMock]:
    """Returns (mongo_client_mock, collection_mock)."""
    mock_collection = MagicMock()
    mock_collection.count_documents.return_value = existing_count

    mock_db = MagicMock()
    mock_db.__getitem__.return_value = mock_collection

    mock_client_instance = MagicMock()
    mock_client_instance.__getitem__.return_value = mock_db
    mock_client_instance.__enter__.return_value = mock_client_instance
    mock_client_instance.__exit__.return_value = False

    mongo_client_mock = mocker.patch(
        "ai_etl.destinations.mongodb_dest.MongoClient", return_value=mock_client_instance
    )
    return mongo_client_mock, mock_collection


def test_save_mongodb_missing_env_raises(monkeypatch) -> None:
    monkeypatch.delenv("MONGODB_URI", raising=False)
    with pytest.raises(EnvironmentError, match="MONGODB_URI"):
        save_mongodb(_DF, "shop", "output")


def test_save_mongodb_replace_clears_then_inserts(mocker, monkeypatch: pytest.MonkeyPatch) -> None:
    # if_exists="replace" never evaluates the "fail" branch's own
    # count_documents check (short-circuited by the if_exists comparison),
    # so the mock only needs to answer the one post-insert count call.
    monkeypatch.setenv("MONGODB_URI", "mongodb://shared-host:27017")
    _, mock_collection = _mock_client(mocker, existing_count=3)

    result = save_mongodb(_DF, "shop", "output", if_exists="replace")

    mock_collection.delete_many.assert_called_once_with({})
    mock_collection.insert_many.assert_called_once_with(_DF.to_dict(orient="records"))
    assert result == {"rows_loaded": 3, "destination": "shop.output"}


def test_save_mongodb_fail_raises_when_collection_not_empty(
    mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://shared-host:27017")
    _, mock_collection = _mock_client(mocker, existing_count=5)

    with pytest.raises(ValueError, match="already has documents"):
        save_mongodb(_DF, "shop", "output", if_exists="fail")

    mock_collection.insert_many.assert_not_called()


def test_save_mongodb_append_does_not_clear(mocker, monkeypatch: pytest.MonkeyPatch) -> None:
    # Empty collection to start, same as postgres_dest.py's own append
    # coverage — appending onto a *non-empty* collection hits the same
    # "final count != len(df)" mismatch check postgres_dest.py's `to_sql`
    # equivalent has for pre-existing rows; not this test's concern.
    monkeypatch.setenv("MONGODB_URI", "mongodb://shared-host:27017")
    _, mock_collection = _mock_client(mocker, existing_count=3)

    result = save_mongodb(_DF, "shop", "output", if_exists="append")

    mock_collection.delete_many.assert_not_called()
    mock_collection.insert_many.assert_called_once_with(_DF.to_dict(orient="records"))
    assert result["rows_loaded"] == 3


def test_save_mongodb_uses_tenant_override_over_shared_env(
    mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://shared-host:27017")
    mongo_client_mock, _ = _mock_client(mocker, existing_count=3)

    with tenant_connections({"mongodb": "mongodb://tenant-host:27017"}):
        save_mongodb(_DF, "shop", "output")

    mongo_client_mock.assert_called_once_with("mongodb://tenant-host:27017")


def test_preview_mongodb_missing_env_raises(monkeypatch) -> None:
    monkeypatch.delenv("MONGODB_URI", raising=False)
    with pytest.raises(EnvironmentError, match="MONGODB_URI"):
        preview_mongodb(_DF, "shop", "output")


def test_preview_mongodb_reports_existing_count_and_never_writes(
    mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://shared-host:27017")
    _, mock_collection = _mock_client(mocker, existing_count=7)

    result = preview_mongodb(_DF, "shop", "output")

    assert result == {
        "destination_type": "mongodb",
        "destination": "shop.output",
        "would_write_rows": 3,
        "existing": {"existing_rows": 7},
    }
    mock_collection.insert_many.assert_not_called()
    mock_collection.delete_many.assert_not_called()


def test_preview_mongodb_unreachable_reports_none(mocker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://shared-host:27017")
    mocker.patch(
        "ai_etl.destinations.mongodb_dest.MongoClient", side_effect=ConnectionError("no route")
    )

    result = preview_mongodb(_DF, "shop", "output")

    assert result["existing"] is None
