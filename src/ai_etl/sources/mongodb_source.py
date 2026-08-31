"""MongoDB source connector (Sprint 11, ADR-012).

The one `sources/` connector backed by a document store instead of a SQL
engine — no SQLAlchemy dialect applies, so this talks to `pymongo`
(the official driver) directly. Otherwise follows the same shape as every
other connector: a module-level `load_<type>(...) -> pd.DataFrame`, a
connection string read from an env var (`MONGODB_URI`, same convention as
`postgres_source.py`'s `POSTGRES_URL`/`mysql_source.py`'s `MYSQL_URL`), no
source-level `try/except` (errors propagate to `agents/pipeline/extractor.py`'s
single catch point).

**Schema inference for a schema-less store**: MongoDB documents in the same
collection can have different shapes (no fixed columns to read up front,
unlike a SQL table). This connector doesn't add a separate schema-sampling
step — `pd.json_normalize()` on the fetched documents already does the
equivalent of sampling: it flattens each document's keys into columns and
unions them across every document it's given, filling `NaN` where a given
document lacks a key. `limit` (default `MONGODB_SAMPLE_LIMIT`, see below)
controls how many documents that union is built from, which doubles as the
same purpose `agents/pipeline/extractor.py::_extract_schema`'s `df.head(3)` sample
serves for the other connectors — a bound on how much of a
possibly-huge/heterogeneous collection gets pulled into a single
`pipeline_plan` run and inspected for schema.
"""

import os
from typing import Any

import pandas as pd
from pymongo import MongoClient

from ai_etl.core.tenant_context import get_connection_override

# Ceiling on documents fetched per `load_mongodb` call when the caller
# doesn't pass an explicit `limit` — MongoDB collections have no inherent
# size bound the way a `pipeline_plan`-scoped CSV/SQL query implicitly does,
# so an unset limit could otherwise pull an entire large collection into
# memory. Mirrors `document_source.py`'s `MAX_TEXT_CHARS` bound for the same
# reason: keep a connector usable by default without requiring every caller
# to reason about an unbounded external data source.
DEFAULT_SAMPLE_LIMIT = 10_000

# Rejects MongoDB server-side JavaScript operators in a caller-supplied
# query filter — the NoSQL-injection-equivalent surface to unparameterized
# SQL for this connector. Shallow check (top-level keys only) since
# `pipeline_plan.sources[].query` is expected to be a plain equality/
# comparison filter, not an arbitrary aggregation pipeline.
_FORBIDDEN_QUERY_OPERATORS = {"$where", "$function", "$accumulator"}


def _validate_query(query: dict[str, Any]) -> None:
    forbidden = _FORBIDDEN_QUERY_OPERATORS & query.keys()
    if forbidden:
        raise ValueError(
            f"Query contains forbidden server-side JS operator(s): {sorted(forbidden)}"
        )


def load_mongodb(
    database: str,
    collection: str,
    query: dict[str, Any] | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Load documents from a MongoDB collection into a DataFrame.

    Uses the current run's tenant-supplied connection string (ADR-044,
    `core/tenant_context.py`) if one is set, otherwise falls back to the
    shared MONGODB_URI env var (ADR-012's original, still-default behavior).
    `query` is an optional MongoDB filter dict (e.g. `{"status": "active"}`);
    server-side-JS operators are rejected. `limit` bounds how many documents
    are fetched (and therefore how the union-of-keys schema is inferred),
    defaulting to `DEFAULT_SAMPLE_LIMIT`.
    """
    uri = get_connection_override("mongodb") or os.getenv("MONGODB_URI")
    if not uri:
        raise EnvironmentError("MONGODB_URI environment variable is not set.")

    filter_query = query or {}
    _validate_query(filter_query)
    effective_limit = limit if limit is not None else DEFAULT_SAMPLE_LIMIT

    with MongoClient(uri) as client:  # type: MongoClient[dict[str, Any]]
        cursor = client[database][collection].find(filter_query).limit(effective_limit)
        documents = list(cursor)

    df = pd.json_normalize(documents)
    if "_id" in df.columns:
        # ObjectId isn't JSON-serializable — audit_log/source_schemas both
        # eventually get JSON-dumped (audit/logger.py), so stringify it here
        # rather than pushing that concern onto every downstream consumer.
        df["_id"] = df["_id"].astype(str)
    return df
