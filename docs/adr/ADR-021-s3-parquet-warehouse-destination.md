# ADR-021 — S3-Parquet as the first data-warehouse destination

**Status:** Accepted
**Date:** 2026-08-21
**Deciders:** Bruno Ribeiro

## Context

Sprint 20's scope (Vault: `artefact/product-roadmap-post-tcc.md`): `destinations/` today only has
`csv_dest.py` and `postgres_dest.py`. The roadmap's own framing matters here: this is explicitly
**not** competing with a customer's data warehouse (`saas-potential.md`'s anti-profile — "não
competir com Snowflake/Matillion") — it is the opposite, an ingestion/transformation layer that
lands its output *into* the warehouse a customer already has. Candidates named in the roadmap:
Snowflake, BigQuery, S3-parquet.

**Investigated first, per this project's own standard, before picking:**

- `boto3>=1.34.0` is already a declared base dependency (Sprint 4, ADR-009, `audit/storage.py`'s
  `S3StorageBackend`) and already proven live against a real bucket (`ai-etl-artifacts-brlla`,
  `sa-east-1`) with credentials resolved via boto3's default chain
  (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_DEFAULT_REGION`, already configured on both
  Railway services). No new credential-management story to invent.
- **ADR-009 already ruled out `pyarrow.fs.S3FileSystem` for this exact environment** — it "hung
  indefinitely... even with correct IAM, reproduced on two independent machines in a sibling
  project," which is why `S3StorageBackend` uses `boto3.client("s3").put_object`/`get_object`
  instead. That finding is directly relevant here and must not be silently re-broken: this ADR's
  destination writes a parquet buffer with `pandas.DataFrame.to_parquet(engine="pyarrow")`
  in-memory, then uploads the resulting `bytes` via `boto3` — `pyarrow` is used only as pandas'
  serialization *engine* (no network I/O of its own), never as `pyarrow.fs.S3FileSystem`.
- Neither Snowflake nor BigQuery has any existing footprint in this codebase: `snowflake-connector-python`
  and `google-cloud-bigquery` would both be entirely new dependencies, each pulling in its own
  auth model (Snowflake: account/user/password or key-pair; BigQuery: a GCP service-account JSON
  key) that this project has never needed to store/rotate/document before. Real, live-tested
  credentials for either are not available in this environment (same "flagged, not faked"
  constraint Sprint 8/14 hit for LLM/webhook providers) — this sprint's "done" bar is explicitly
  "at least 1 destination working **end-to-end, with credentials**," which S3 can actually clear
  here and now.
- `pandas.DataFrame.to_parquet` needs a parquet engine; `pyarrow` is not yet a project dependency
  (only `pandas`/`openpyxl`/`numpy` handle tabular I/O today) — one new dependency either way,
  but a widely-used, pure-serialization one with no server/service of its own to provision,
  narrower in scope than a full vendor SDK.

## Decision

**New `destinations/s3_parquet_dest.py`: `save_s3_parquet(df, bucket, key) -> dict[str, Any]`**,
same shape/contract as `save_csv`/`save_postgres` (`rows_loaded`, `destination` in the returned
dict).

- **Serialization**: `df.to_parquet(buffer, engine="pyarrow", index=False)` into an in-memory
  `io.BytesIO`, then `boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=buffer.getvalue())`
  — mirrors `S3StorageBackend.write_bytes`'s exact I/O shape (buffer → bytes → `put_object`), kept
  as a standalone connector rather than reusing `StorageBackend` because that abstraction is
  scoped to *audit artifacts* under a fixed `{env}/{tenant_id}/...` prefix (ADR-009) — a business
  destination the LLM plans from a user's spec is a different concern with its own bucket/key,
  the same separation `postgres_dest.py` already keeps from `audit/db.py`'s own Postgres usage.
- **Credentials**: boto3's default chain, same as `S3StorageBackend` — `AWS_ACCESS_KEY_ID`/
  `AWS_SECRET_ACCESS_KEY`/`AWS_DEFAULT_REGION`, already live on both Railway services. No new env
  var for credentials. `bucket`/`key` are per-destination values from `pipeline_plan.destination`
  (LLM-parsed from the spec), matching `csv_dest.py`'s `path` and `postgres_dest.py`'s `table` —
  not a single shared env var, since a customer's target bucket/prefix is destination data, not
  deployment config (the same reasoning `sqlite_source.py` used for per-source `path` over a
  single shared env var).
- **Row-count validation after write**: re-reads the object back
  (`pd.read_parquet(BytesIO(get_object(...)["Body"].read()))`) and compares `len()` against the
  source DataFrame, raising `RuntimeError` on mismatch — same verification discipline
  `save_postgres` already applies (`SELECT COUNT(*)` after `to_sql`), adapted to an object store
  that has no query interface of its own.
- **Destination type**: `"s3_parquet"` (not bare `"parquet"` — a future local/`csv`-adjacent
  parquet destination is plausible later and would need its own type name; `s3_parquet` names the
  actual delivery mechanism, avoiding an ambiguous rename later).
- **Dispatch**: `agents/loader.py::loader_node` gains one `elif dest_type == "s3_parquet":` branch,
  additive — the existing `csv`/`postgres` branches and the final `else: raise ValueError(...)`
  path are untouched, same additive pattern Sprint 11 used in `extractor.py`.
  `agents/orchestrator.py::ORCHESTRATOR_PROMPT`'s destination-type list/field docs gain
  `"s3_parquet"` (`bucket`, `key`) alongside `csv`/`postgres`.
- **New dependency**: `pyarrow>=15.0.0` (parquet read/write engine only — no `pyarrow.fs.*` import
  anywhere in this module, per the ADR-009 finding above).

**Why S3-parquet over Snowflake or BigQuery for this sprint:**

| | S3-parquet | Snowflake | BigQuery |
|---|---|---|---|
| New dependency | `pyarrow` (serialization only) | `snowflake-connector-python` (full client) | `google-cloud-bigquery` (full client) |
| New credential type | None — reuses ADR-009's boto3 chain | Account/user/password or key-pair | GCP service-account JSON |
| Live-testable here, with real credentials | Yes — same AWS account as `ai-etl-artifacts-brlla` | No account available in this environment | No account available in this environment |
| Real product fit | Parquet on S3 is a de facto lake-house interchange format most warehouses (Snowflake external tables, BigQuery external tables, Athena, Redshift Spectrum, Databricks) can query directly | Requires the customer to already run Snowflake | Requires the customer to already run BigQuery |
| Meets this sprint's "done" bar (works end-to-end, with credentials) | Yes | Not without provisioning a trial account first | Not without provisioning a trial account first |

Snowflake and BigQuery remain reasonable follow-ups (a customer with an existing warehouse account
is exactly this feature's target user) — flagged in Consequences below, not foreclosed. Landing
S3-parquet first also gives every future warehouse-vendor destination a working precedent for
"same `save_X(df, ...) -> dict` shape, additive `loader.py` dispatch" to follow.

## Consequences

- **Positive**: closes Sprint 20's "at least 1 data-warehouse destination working end-to-end, with
  credentials" bar using infrastructure this project already owns and has live-verified
  (ADR-009's bucket/credentials) — no new account, no new secret to provision or document beyond
  per-pipeline `bucket`/`key` values.
- **Positive**: parquet (columnar, typed, compressed) is a meaningfully different destination
  shape from `csv_dest.py`'s row-oriented text output — real format diversity, not just "another
  S3 client," and directly consumable by external-table features in every major warehouse without
  a load step on the customer's side.
- **Positive**: reuses ADR-009's already-hard-won finding (`boto3`, never `pyarrow.fs.S3FileSystem`)
  instead of risking reintroducing the same hang in a new module.
- **Negative**: `pyarrow` is a genuinely new dependency (not previously transitive anywhere in this
  project, unlike `charset-normalizer` in Sprint 22) — `pip-audit`/`bandit` re-run required, flagged
  and verified in this PR's `make check`, not silently assumed clean.
- **Negative**: Snowflake and BigQuery — the two other roadmap-named candidates — remain
  unimplemented. A customer whose warehouse is one of those two still needs a follow-up sprint;
  S3-parquet is a real, common interchange point for both (external tables) but not a native write
  path into either.
- **Neutral**: no `PipelineState` schema change beyond `pipeline_plan.destination.type` gaining
  `"s3_parquet"` as a valid value (with `bucket`/`key` fields) — same non-invasive shape ADR-012's
  new source types used. No database migration — destination config lives entirely inside
  `pipeline_plan` (LLM-parsed from the spec, `saved_pipelines.spec` is `Text`), same as every
  existing destination type, so there is no new column to add.
