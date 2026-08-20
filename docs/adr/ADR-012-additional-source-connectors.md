# ADR-012 — SQLite connector and authenticated REST as new `sources/` connectors

**Status:** Accepted (amended same day — see Addendum)
**Date:** 2026-08-19
**Deciders:** Bruno Ribeiro

> **Addendum (2026-08-19, same session):** the three items this ADR originally scoped out as follow-ups — MySQL/MariaDB, MongoDB, and OAuth2 client-credentials — were added on owner request before this PR closed, still within Sprint 11's scope (not a new sprint). See the "Addendum" section near the end for what changed and why the original reasoning for deferring them no longer fully applies. The body below is left as originally written so the initial trade-off reasoning stays legible; treat the Addendum as authoritative wherever the two disagree.

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
- ~~**Negative**: only one new DB engine...~~ — superseded by the Addendum below (MySQL/MariaDB and MongoDB were added the same day).
- ~~**Negative**: OAuth2 client-credentials...~~ — superseded by the Addendum below (implemented the same day).
- **Neutral**: no database migration, no new agent, no change to `PipelineState`'s shape beyond `pipeline_plan.sources[].type` gaining `"sqlite"` as a valid value and the existing `"rest"` entry gaining an optional `auth` key. (Still true post-Addendum — `"mysql"`/`"mongodb"` are two more valid `type` values, `auth` gains a fourth shape, same non-migration.)

---

## Addendum (2026-08-19) — MySQL/MariaDB, MongoDB, and OAuth2 client-credentials

The original decision above scoped SQLite as *the* new engine for this sprint and left MySQL/MariaDB, MongoDB, and OAuth2 client-credentials as flagged follow-ups — reasonable at the time given the "at least 1 engine + 1 auth pattern, both real end-to-end" bar and the cost of standing up new CI service containers. The owner asked for all three to land in this same PR/sprint before merge. This addendum records what changed and why the original deferral reasoning doesn't block that.

### MySQL/MariaDB — added as `sources/mysql_source.py`

`load_mysql(table: str, query: str | None = None) -> pd.DataFrame`, same shape as `load_postgres` — single shared `MYSQL_URL` env var (MySQL/MariaDB are server processes like Postgres, unlike SQLite's per-source file path), table-name allowlist regex (dots allowed — `database.table` — unlike SQLite's), SQL only via SQLAlchemy `text()`.

- **Driver**: `pymysql` (pure Python, `mysql+pymysql://` SQLAlchemy dialect) — no compiled/system dependency, matching `psycopg2-binary`'s "drop-in" bar for Postgres. New `pyproject.toml` dependency; `pip-audit`/`bandit` re-run clean after adding it (see PR's `make check` summary).
- **MySQL vs. MariaDB**: not distinguished as separate connector types. MariaDB is wire-protocol-compatible with `pymysql`/`mysql+pymysql`, so the same `load_mysql`/`MYSQL_URL` works unchanged against either engine — the original ADR's observation that "MySQL is mostly `postgres_source.py` with a different connection string" is exactly why one connector serves both, rather than justifying skipping it.
- **Real tests, not just mocks**: the original concern was CI infrastructure cost. `tests/integration/test_mysql_source_real.py` follows `test_audit_persistence.py`'s established self-skip-if-unreachable convention (not a new pattern), against `mysql-test` — a new `docker-compose.yml` service (`make mysql-test-up`) and a new `check` job service container in `.github/workflows/ci.yml` (deliberately added to `check`, not `e2e` — see that file's inline comment on why this doesn't reopen the Postgres-unblocking concern the original `e2e`-job split was about). `tests/unit/test_mysql_source.py` covers table-name validation and the missing-`MYSQL_URL` path without any server, mirroring `postgres_source.py`'s own unit-test split (`tests/unit/test_sources.py` covers only its validation function directly — `load_postgres`'s connection path was never unit-tested, only integration/e2e-tested against real Postgres; `load_mysql` follows the identical split).

### MongoDB — added as `sources/mongodb_source.py`

`load_mongodb(database: str, collection: str, query: dict | None = None, limit: int | None = None) -> pd.DataFrame` — the one connector backed by a document store, not SQL.

- **Driver**: `pymongo` (official driver) — new `pyproject.toml` dependency (pulls in `dnspython` transitively for `mongodb+srv://` URIs). No SQLAlchemy dialect exists for a document store, so this connector talks to `pymongo` directly rather than through `create_engine`/`text()` — the one connector in `sources/` that isn't SQLAlchemy-mediated, alongside `csv_source.py` (no DB at all) and `document_source.py` (LLM-structured, ADR-010).
- **Schema inference from schema-less documents**: no separate sampling step was added. `pd.json_normalize()` on the fetched documents already unions every document's keys into columns, filling `NaN` where a given document lacks one — this *is* the sampling step, and it's exactly `agents/extractor.py::_extract_schema`'s `df.head(3)` sample serving the same "bound what a heterogeneous source pours into schema/audit output" purpose the other connectors already have. `limit` (default `DEFAULT_SAMPLE_LIMIT = 10_000`, mirroring `document_source.py`'s `MAX_TEXT_CHARS` bound for the same "don't let one connector default to unbounded" reason) controls how many documents that union is built from.
- **NoSQL-injection-equivalent guard**: `query` (a MongoDB filter dict) is checked against a shallow denylist of server-side-JS operators (`$where`, `$function`, `$accumulator`) before use — this connector's analogue to SQL `text()`-parameterization, since MongoDB has no bind-parameter mechanism to enforce the same way for a whole filter dict; this is the one connector-specific security control the other three DB connectors don't need.
- **Real tests**: `tests/integration/test_mongodb_source_real.py`, same self-skip convention, against a new `mongodb-test` service (`docker-compose.yml`, `make mongodb-test-up`, `check` job in CI) — including a test seeding two *heterogeneously shaped* documents (one has a `tags` field, one doesn't) to prove the union-of-keys schema inference for real, not just against uniform documents. `tests/unit/test_mongodb_source.py` mocks only `pymongo.MongoClient` (the network transport), exercising the real `json_normalize`/`_id`-stringification/query-validation logic — same "mock only the external transport" convention `rest_source.py`'s own tests already established.

### OAuth2 client-credentials — added to `rest_source.py`'s `auth`

A fourth `auth["type"]`: `oauth2_client_credentials`. Config shape: `{"type": "oauth2_client_credentials", "token_url": "...", "client_id_env_var": "NAME", "client_secret_env_var": "NAME", "scope": "..." (optional)}`. `client_id`/`client_secret` are still only ever env-var *names*, never literal secrets, same convention as `api_key`/`bearer`/`basic`; `token_url`/`scope` aren't secrets, so they're plain values in `pipeline_plan.sources[].auth` itself (not env-var-indirected) — consistent with `postgres_source.py`'s own split (`POSTGRES_URL` is a secret-bearing env var; a `table` name is a plain config value).

- **Token fetch**: `_fetch_oauth2_token()` POSTs the standard `grant_type=client_credentials` body (RFC 6749 §4.4) to `token_url`, reads `access_token`/`expires_in` from the JSON response.
- **Caching**: module-level `_TOKEN_CACHE: dict[(token_url, client_id), (token, expires_at)]`, keyed so distinct client credentials against the same token endpoint don't collide. `expires_at` is tracked via `time.monotonic()` (immune to wall-clock adjustments) with a 30-second re-fetch leeway before the token endpoint's reported expiry, defaulting to a 3600-second TTL if `expires_in` is omitted. This is a new pattern in `sources/` — every other connector here is stateless between calls — justified because re-fetching a token on every `load_rest` call (rather than once per its real TTL) would be both wasteful and a plausible rate-limit trigger against a real OAuth2 provider; the cache is process-local, not persisted, so a Celery worker restart or a different worker process starts cold, which is an acceptable cost for the caching's actual purpose (avoiding same-process, same-run token-endpoint hammering, not cross-run persistence).
- **Why this is fine to add now** (the original ADR's stated reason to defer it — "a meaningfully bigger unit of work" — was correct in scope, just not in priority once the owner asked for it): the token-fetch-and-cache logic is fully unit-testable without a real OAuth2 provider (`tests/unit/test_rest_source_auth.py`'s OAuth2 section mocks only `httpx.post`, exercising the real caching/expiry/cache-key logic, including a test that monkeypatches `time.monotonic` to prove a token is correctly re-fetched past its TTL) — no new CI infrastructure was actually needed for this one, unlike MySQL/MongoDB.

### Addendum wiring

`agents/extractor.py::extractor_node` gains `mysql`/`mongodb` dispatch branches (`elif source_type == "mysql": df = load_mysql(source["table"], source.get("query"))`; `elif source_type == "mongodb": df = load_mongodb(source["database"], source["collection"], source.get("query"), source.get("limit"))`) — dispatch signature unchanged, same pattern as every prior addition. `agents/orchestrator.py::ORCHESTRATOR_PROMPT` documents `mysql`/`mongodb` as source types and the `oauth2_client_credentials` auth shape, with the same explicit instruction as the other auth shapes: only populate `auth`/credential fields when the spec names them, never invent a literal secret.

### Addendum consequences

- **Positive**: closes the "bem mais banco de dados" (plural) reading of the owner's original request literally, not just SQLite alone — three database engines now (Postgres, SQLite, MySQL/MariaDB) plus a document store (MongoDB), and REST auth covers both static-credential (`api_key`/`bearer`/`basic`) and token-issuing (`oauth2_client_credentials`) patterns.
- **Positive**: MySQL/MongoDB real-test coverage follows the exact self-skip convention already established by `test_audit_persistence.py`, not a new testing pattern — the only genuinely new pattern introduced is OAuth2's in-process token cache.
- **Negative**: two new runtime dependencies (`pymysql`, `pymongo` + its `dnspython` transitive dependency) — `pip-audit`/`bandit` re-run clean (see PR summary), but this does grow the dependency surface `pip-audit` needs to keep clean going forward, unlike SQLite's zero-new-dependency story.
- **Negative**: this environment (the agent sandbox this ADR was authored in) has no Docker available, so `tests/integration/test_mysql_source_real.py`/`test_mongodb_source_real.py` could only be verified by code review + the self-skip path locally, not a real run against `mysql-test`/`mongodb-test` — CI (which does have Docker service containers) is the actual gate for these two, same "CI is the real gate" caveat already used for prior sprints' sandbox-hang workaround, just for a different reason (missing Docker, not the pytest/mypy hang).
- **Neutral**: OAuth2's `_TOKEN_CACHE` is the first piece of *process-local mutable state* in `sources/` — every other connector is a pure function of its arguments/environment. Worth knowing if a future connector is tempted to add similar caching: this one exists because a real token-issuing round trip has a cost/rate-limit an env-var lookup or a DB connection doesn't.

## Related

- Vault: `artefact/sprint-roadmap.md` — Sprint 11 scope and the owner's request that motivated it.
- `docs/adr/ADR-010-document-source-pdf-docx.md` — the no-new-agent precedent this ADR confirms still holds.
- `src/ai_etl/sources/postgres_source.py` — the connector pattern `sqlite_source.py` follows, and the `POSTGRES_URL`-env-var convention `rest_source.py`'s `auth` follows for its own env-var lookups.
- `src/ai_etl/sources/rest_source.py`, `src/ai_etl/agents/extractor.py`, `src/ai_etl/agents/orchestrator.py` — files this ADR's decision is wired into.
- `src/ai_etl/sources/mysql_source.py`, `src/ai_etl/sources/mongodb_source.py` — Addendum connectors.
- `tests/integration/test_mysql_source_real.py`, `tests/integration/test_mongodb_source_real.py` — real, self-skipping-if-unreachable coverage for the Addendum's two new DB engines, same convention as `tests/integration/test_audit_persistence.py`.
- `docker-compose.yml` (`mysql-test`/`mongodb-test` services), `.github/workflows/ci.yml` (`check` job's new service containers) — infrastructure the Addendum's real tests run against.

> **A numbering note for whoever merges this** (left here deliberately, not resolved by this PR): Sprints 10 (multi-cloud) and 12 (scale/robustness), running in parallel with Sprint 11, also produced an `ADR-012` for their own topics. This is a real numbering collision across three parallel branches, to be resolved by sequential renumbering at merge time — not something this PR should preemptively guess at, since the final order depends on merge order the author of this PR doesn't control.
