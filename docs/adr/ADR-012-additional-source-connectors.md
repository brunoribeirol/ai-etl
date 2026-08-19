# ADR-012 — SQLite connector and authenticated REST as new `sources/` connectors

**Status:** Accepted
**Date:** 2026-08-19
**Deciders:** Bruno Ribeiro

---

## Context

Sprint 11's scope (Vault: `artefact/sprint-roadmap.md`) is explicit owner direction (17/08/2026, confirming a suggestion from the advisor on 14/08): *"não quero apenas PostgreSQL. Quero que tenha bem mais banco de dados e que tenha REST APIs e outros que você achar interessante."* Today `sources/` has four connectors — `csv_source.py`, `postgres_source.py`, `rest_source.py`, `document_source.py` (ADR-010) — but only one database engine (PostgreSQL), and `rest_source.py` only supports unauthenticated public endpoints (the Open-Meteo call in case-study scenario 3).

Two additions needed deciding:

1. **Which new database engine?** The roadmap listed candidates: MySQL/MariaDB, SQLite, MongoDB.
2. **How does REST auth get added?** Extend `rest_source.py` in place, or a new module?

## Decision

### 1. New database engine: SQLite (not MySQL/MariaDB or MongoDB)

`sources/sqlite_source.py` adds `load_sqlite(path: str, table: str, query: str | None = None) -> pd.DataFrame`, same shape as `load_postgres`.

**Why SQLite over MySQL/MariaDB or MongoDB:**

- **File-based, no server process.** MySQL/MariaDB would need a `docker-compose` service (mirroring `postgres`/`postgres-test`) and a CI `services:` block (mirroring `.github/workflows/ci.yml`'s `e2e` job) before a single test could run against it for real. SQLite needs neither — `sqlalchemy.create_engine("sqlite:///<path>")` against a plain file is enough, and the standard library's `sqlite3` module can seed that file directly in a test's `tmp_path` fixture. This lets Sprint 11's connector get **real, non-mocked, engine-to-engine tests** (`tests/unit/test_sqlite_source.py` seeds an actual `.db` file and reads it back) at zero added CI infrastructure — a stronger "real test" story than adding a second Postgres-shaped service would have bought for the same effort.
- **A genuinely different engine shape, not a Postgres clone.** MySQL/MariaDB is protocol-compatible enough with the existing `postgres_source.py` pattern (network server, `user:pass@host/db` URL, SQLAlchemy dialect swap) that adding it would mostly duplicate `postgres_source.py` with a different connection string — useful coverage, but not a meaningfully different integration shape. SQLite (embedded, file-based, single-writer) and MongoDB (document store, no SQL) are the two candidates that exercise a genuinely different connector shape. Between those two, SQLite keeps `load_sqlite`'s output tabular via `pd.read_sql` with zero new dependencies (`sqlite3` is stdlib) and zero new document→table mapping logic; MongoDB would require a `pymongo` dependency, a running Mongo instance for real tests (same CI-infrastructure cost as MySQL), and a schema-inference step to flatten documents into `pipeline_plan`'s tabular `source_schemas` contract — a bigger lift for a case-study-scoped sprint whose "done" bar is "at least 1 new engine working end-to-end with real tests."
- **Real product relevance, not just a test-convenience pick.** SQLite is a common source for small-business exports, embedded app data, and local analytics tooling — a legitimate "another database" for the kind of user this framework targets, not merely the easiest option to wire up.

MySQL/MariaDB and MongoDB remain reasonable follow-ups (flagged in Consequences below) if a future sprint's case study specifically needs them — this ADR doesn't foreclose that, it just doesn't block Sprint 11 on standing up new CI service containers.

**Table name validation**: same allowlist-regex pattern as `postgres_source.py`/`postgres_dest.py`, but stricter — no `.` allowed, since SQLite has no `schema.table` notion. SQL only via SQLAlchemy `text()`, never f-strings, same as every other connector (`# nosec B608` comment follows the existing convention, since the interpolated identifier is validated immediately above, not a bound value).

**Per-source `path`, not an env var.** `postgres_source.py` reads a single `POSTGRES_URL` from the environment because Postgres is a server every source in a run typically shares. SQLite is file-based — different sources in the same `pipeline_plan.sources` list may legitimately point at different `.db` files — so `path` is a per-source argument, matching `csv_source.py`'s `path` convention rather than `postgres_source.py`'s single-shared-env-var one.

### 2. REST auth: extend `rest_source.py` in place (not a new module)

`load_rest` gains an optional `auth: dict[str, Any] | None = None` parameter. Three shapes supported, dispatched by `auth["type"]`:

- `api_key` — `{"type": "api_key", "header": "X-API-Key", "env_var": "NAME"}` (header name defaults to `X-API-Key`)
- `bearer` — `{"type": "bearer", "env_var": "NAME"}`
- `basic` — `{"type": "basic", "username_env_var": "NAME", "password_env_var": "NAME"}`

`auth` never carries a literal secret — every shape references an **environment variable name**, read at call time via `os.getenv`, mirroring `postgres_source.py`'s `POSTGRES_URL` lookup and this project's non-negotiable "no hardcoded credentials" rule. A missing env var raises `EnvironmentError` immediately (same failure mode `load_postgres` already has for a missing `POSTGRES_URL`), which `extractor_node`'s existing single catch point turns into `state["error"]` — no new error-handling path needed.

**Why extend in place rather than a new module:** `load_rest`'s signature grows by one optional, backward-compatible parameter (`auth: ... = None`) — every existing call site (scenario 3's e2e test, `rest_source.py`'s own no-auth tests) keeps working unchanged. Splitting auth into a separate `authenticated_rest_source.py` would duplicate the JSON-normalization logic that has nothing to do with authentication, and would force `extractor.py`'s dispatch to distinguish "rest" from "rest with auth" as two source types — an artificial split `pipeline_plan.sources[].type` shouldn't need to make, since auth is a per-call detail of the same HTTP GET, not a different source shape.

**Why not OAuth2 (client-credentials) as a fourth type in this sprint:** true OAuth2 needs a token-fetch-and-cache step (a token endpoint call, expiry tracking, refresh) that's a meaningfully bigger unit of work than the other three schemes, which are all "attach this static header/credential." `api_key`/`bearer`/`basic` already satisfy the roadmap's "REST com autenticação real (API-key e/ou OAuth)" bar with the "e/ou" read as "at least one real non-public-endpoint auth pattern" — bearer-token auth is exactly the shape an OAuth2 access token takes once already issued, so a caller with an externally-obtained OAuth token uses `bearer` today. A dedicated token-fetching OAuth2 client-credentials flow is flagged as a follow-up, not silently dropped.

### 3. No new LangGraph agent (same decision as ADR-010)

Both additions are pure I/O — `load_sqlite` is a synchronous SQL read; `_build_auth` builds headers/auth kwargs, no LLM call, no multi-step reasoning. Neither meets ADR-010's bar for the one connector that *does* need agent-adjacent LLM structuring (`document_source.py`). `sources/sqlite_source.py` follows the `csv_source.py`/`postgres_source.py` pattern exactly (module-level `load_<type>`, no class/protocol, no source-level `try/except`); `rest_source.py`'s auth addition doesn't change its shape at all. No motive to diverge from ADR-010's precedent was found — documented here explicitly, as this ADR's introduction required.

**Wiring**: `agents/extractor.py::extractor_node`'s dispatch gains `elif source_type == "sqlite": df = load_sqlite(source["path"], source["table"], source.get("query"))`, and the existing `rest` branch passes through `source.get("auth")`. `agents/orchestrator.py::ORCHESTRATOR_PROMPT`'s source-type list gains `"sqlite"` (with `path`/`table`) and documents `rest`'s optional `auth` shapes — explicitly instructing the LLM to only populate `auth` when the spec names an env var, never to invent a literal secret value.

## Consequences

- **Positive**: closes Sprint 11's "at least 1 new DB engine + 1 new REST auth pattern, both real end-to-end" bar without new CI infrastructure, new dependencies, or a new database service to provision — `sqlite3`/`sqlalchemy`'s SQLite dialect are already available (stdlib + existing `sqlalchemy` dependency).
- **Positive**: `load_sqlite` and `_build_auth` are both testable without mocks (SQLite: real file I/O; REST auth: header/auth-object construction is pure logic, independently testable from the network call itself) — meaningfully stronger test coverage than a mocked-network-only story.
- **Positive**: `auth`'s env-var-reference shape composes with `POSTGRES_URL`'s existing convention and this project's `.env.example` documentation pattern — no new secret-handling convention introduced.
- **Negative**: only one new DB engine, not the "bem mais banco de dados" (plural) the owner's request literally suggests. MySQL/MariaDB and MongoDB remain open follow-ups — flagged for a future sprint if the case study or a real tenant specifically needs one of them, at the cost of a new CI service container (MySQL) or a new dependency + document-to-tabular mapping step (MongoDB).
- **Negative**: OAuth2 client-credentials (token-fetch-and-cache) is not implemented — `bearer` covers an already-issued OAuth access token, but not the token-acquisition flow itself. Flagged as a follow-up, not silently dropped.
- **Neutral**: no database migration, no new agent, no change to `PipelineState`'s shape beyond `pipeline_plan.sources[].type` gaining `"sqlite"` as a valid value and the existing `"rest"` entry gaining an optional `auth` key.

## Related

- Vault: `artefact/sprint-roadmap.md` — Sprint 11 scope and the owner's request that motivated it.
- `docs/adr/ADR-010-document-source-pdf-docx.md` — the no-new-agent precedent this ADR confirms still holds.
- `src/ai_etl/sources/postgres_source.py` — the connector pattern `sqlite_source.py` follows, and the `POSTGRES_URL`-env-var convention `rest_source.py`'s `auth` follows for its own env-var lookups.
- `src/ai_etl/sources/rest_source.py`, `src/ai_etl/agents/extractor.py`, `src/ai_etl/agents/orchestrator.py` — files this ADR's decision is wired into.
