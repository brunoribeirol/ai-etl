"""MongoDB destination connector.

The one `destinations/` connector backed by a document store instead of a
SQL engine — no SQLAlchemy dialect applies, so this talks to `pymongo`
directly, mirroring `sources/mongodb_source.py`'s own reasoning. Otherwise
follows the same `save_<type>`/`preview_<type>` shape and `if_exists`
contract as `postgres_dest.py`/`mysql_dest.py`, adapted to a collection
having no fixed schema: `if_exists="replace"`/`"delete_rows"` both mean
"clear the collection first" (there's no table to drop and recreate the
way a SQL engine has); `"fail"` refuses to write into a non-empty
collection; `"append"` inserts without clearing.
"""

import os
from typing import Any, Literal, cast

import pandas as pd
from pymongo import MongoClient

from ai_etl.core.tenant_context import get_connection_override


def save_mongodb(
    df: pd.DataFrame,
    database: str,
    collection: str,
    if_exists: Literal["fail", "replace", "append", "delete_rows"] = "replace",
) -> dict[str, Any]:
    """Save DataFrame rows as documents in a MongoDB collection. Validates
    the collection's total document count after load, same "count mismatch
    raises" contract `postgres_dest.py::save_postgres` uses.

    Uses the current run's tenant-supplied connection string (ADR-044,
    `core/tenant_context.py`) if one is set, otherwise falls back to the
    shared MONGODB_URI env var.
    """
    uri = get_connection_override("mongodb") or os.getenv("MONGODB_URI")
    if not uri:
        raise EnvironmentError("MONGODB_URI environment variable is not set.")

    # pandas types this as list[dict[Hashable, Any]] — every real DataFrame
    # column label is a str (enforced elsewhere in this codebase), so this
    # cast documents an invariant rather than papering over a real mismatch.
    records = cast("list[dict[str, Any]]", df.to_dict(orient="records"))
    with MongoClient(uri) as client:  # type: MongoClient[dict[str, Any]]
        coll = client[database][collection]

        if if_exists == "fail" and coll.count_documents({}) > 0:
            raise ValueError(
                f"Collection '{database}.{collection}' already has documents (if_exists='fail')."
            )
        if if_exists in ("replace", "delete_rows"):
            coll.delete_many({})

        if records:
            coll.insert_many(records)

        count = coll.count_documents({})

    if count != len(df):
        raise RuntimeError(f"Load count mismatch: expected {len(df)}, got {count}")

    return {"rows_loaded": count, "destination": f"{database}.{collection}"}


def preview_mongodb(df: pd.DataFrame, database: str, collection: str) -> dict[str, Any]:
    """Sprint 27 (ADR-028) pattern, ported here — what `save_mongodb` would
    do, without writing anything. Never calls `insert_many`/`delete_many` —
    reads the collection's current document count (if it's reachable) for
    the diff.

    `existing_rows` is `None` if the collection/database doesn't exist yet
    or the connection itself fails — distinguished from "0 existing rows"
    (a real, empty collection) so the preview doesn't claim certainty it
    doesn't have.
    """
    uri = get_connection_override("mongodb") or os.getenv("MONGODB_URI")
    if not uri:
        raise EnvironmentError("MONGODB_URI environment variable is not set.")

    existing_rows: int | None = None
    try:
        with MongoClient(uri) as client:  # type: MongoClient[dict[str, Any]]
            existing_rows = client[database][collection].count_documents({})
    except Exception:  # nosec B110 — unreachable/nonexistent collection, not a preview error
        existing_rows = None

    return {
        "destination_type": "mongodb",
        "destination": f"{database}.{collection}",
        "would_write_rows": len(df),
        "existing": {"existing_rows": existing_rows} if existing_rows is not None else None,
    }
