"""Integration test for the MongoDB source connector (Sprint 11, ADR-012).

Exercises `sources/mongodb_source.py::load_mongodb` against a live MongoDB —
`mongodb-test` in docker-compose.yml (`make mongodb-test-up`). Skipped
automatically when that database isn't reachable, matching
`test_audit_persistence.py`'s skip convention — so `make test`/CI keep
passing on machines without Docker.
"""

import os
import uuid
from collections.abc import Iterator

import pandas as pd
import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from ai_etl.sources.mongodb_source import load_mongodb

_TEST_MONGODB_URI = os.getenv("TEST_MONGODB_URI", "mongodb://localhost:27018")


def _database_reachable() -> bool:
    try:
        client: MongoClient = MongoClient(_TEST_MONGODB_URI, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        client.close()
        return True
    except PyMongoError:
        return False


pytestmark = pytest.mark.skipif(
    not _database_reachable(),
    reason="mongodb-test not reachable; run `make mongodb-test-up` to enable this test",
)


@pytest.fixture
def mongo_orders_collection(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    monkeypatch.setenv("MONGODB_URI", _TEST_MONGODB_URI)
    database = "testdb"
    collection = f"orders_{uuid.uuid4().hex[:8]}"

    client: MongoClient = MongoClient(_TEST_MONGODB_URI)
    client[database][collection].insert_many(
        [
            {"product": "A", "revenue": 100.0, "tags": ["sale"]},
            {"product": "B", "revenue": 200.0},  # heterogeneous shape — no "tags" key
        ]
    )
    yield database, collection
    client[database][collection].drop()
    client.close()


def test_load_mongodb_reads_collection_for_real(mongo_orders_collection: tuple[str, str]) -> None:
    database, collection = mongo_orders_collection
    df = load_mongodb(database, collection)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert set(df["product"]) == {"A", "B"}
    # Heterogeneous documents: json_normalize unions keys, missing ones -> NaN
    assert "tags" in df.columns
    assert df.loc[df["product"] == "B", "tags"].isna().all()
    # ObjectId stringified for JSON-serializability downstream
    assert df["_id"].apply(lambda v: isinstance(v, str)).all()


def test_load_mongodb_query_filter_for_real(mongo_orders_collection: tuple[str, str]) -> None:
    database, collection = mongo_orders_collection
    df = load_mongodb(database, collection, query={"product": "A"})

    assert len(df) == 1
    assert df.iloc[0]["product"] == "A"


def test_load_mongodb_limit_for_real(mongo_orders_collection: tuple[str, str]) -> None:
    database, collection = mongo_orders_collection
    df = load_mongodb(database, collection, limit=1)

    assert len(df) == 1
