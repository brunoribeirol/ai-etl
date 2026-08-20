# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| `main` branch | ✅ |
| Older tags | ❌ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email **araujoribeiro.bruno@gmail.com** with:

1. A description of the vulnerability and its potential impact.
2. Steps to reproduce or a proof-of-concept.
3. The commit hash you tested against.

You will receive an acknowledgement within 72 hours.

## Security design principles

### Code execution
- `exec()` is used at three call sites, each with its own restricted global
  namespace — LLM-generated code is never executed with unrestricted globals
  at any of them:
  - `src/ai_etl/core/sandbox.py` — Transformer agent; `pandas`, `numpy`.
  - `src/ai_etl/agents/analyst.py` — Analyst/Gold agent; `pandas`, `numpy`,
    `plotly`; also permits `setattr`/`vars`, which the sandbox above does not.
  - `src/ai_etl/agents/science.py` — Science agent; same as `analyst.py`,
    plus pre-injected `sklearn` and `statsmodels` classes.
- These three whitelists are maintained independently and are **not**
  identical — see [ADR-003](docs/adr/ADR-003-exec-sandbox.md) for the full
  comparison and the open follow-up to unify them.
- Known limitation, applies to all three sites: restricted `exec()` globals
  can be bypassed via Python introspection (`__class__.__mro__`). This is
  accepted for the current scope with controlled datasets. See inline
  comments in `sandbox.py`, `analyst.py`, and `science.py`.
- Known gap: none of the three sites enforce an actual execution timeout —
  `timeout_seconds` parameters accepted by some callers are not applied.
- **Update (ADR-007):** all three sites now route through a single
  `execute_in_sandbox()` in `sandbox.py`, running in an isolated child
  process (`multiprocessing`, `spawn` context) with a real, enforced
  `timeout_seconds`. That child clears `os.environ` before any user code
  executes, so even the introspection bypass above can no longer reach real
  secrets (`APP_DATABASE_URL`, `OPENAI_API_KEY`-style vars, Clerk config)
  through it — the child never has them in the first place. This closes the
  env-var-exposure angle of the introspection limitation; the introspection
  bypass itself (arbitrary code execution within the child process) remains
  accepted for the current scope.

### SQL
- SQL queries use SQLAlchemy bound parameters (`text("... WHERE id = :id")`)
  wherever the query shape is fixed at call time.
- Table names interpolated into SQL (`sources/postgres_source.py`,
  `destinations/postgres_dest.py`) go through a regex allowlist
  (`^[A-Za-z0-9_.]+$`) before use — bound parameters cannot parameterize
  identifiers in SQLAlchemy, so this is a deliberate, reviewed exception, not
  an oversight. Both sites carry `# nosec B608` with a comment pointing to
  the validation. Unvalidated f-strings in SQL are forbidden; validated,
  identifier-only f-strings behind the allowlist are the one accepted
  pattern — enforced via code review and bandit.

### Secrets and credentials
- API keys, tokens, and passwords are configured via environment variables only.
- `.env` is git-ignored and must never be committed.
- The audit logger (`src/ai_etl/audit/logger.py`) automatically redacts values
  whose keys contain `key`, `token`, `secret`, `password`, or `credential`.
- LLM prompts and generated code are never logged in full — only metadata
  (agent name, action, attempt count, output shape).

### Database access control (Supabase)
- **Row Level Security (RLS) must be enabled on every table in the
  production database, even though this project never uses Supabase's
  client SDK or its auto-generated PostgREST/GraphQL API.** Supabase
  creates the `anon`/`authenticated` roles and grants them full CRUD on
  every `public` schema table **by default on every new project** — those
  grants exist whether or not the app ever uses them, and Supabase's
  PostgREST API is reachable by default using the project's `anon` key
  (which is not treated as a secret by Supabase's own design — safe to
  embed client-side, which also means it circulates more freely than a
  password). Leaving RLS off is a live, remotely exploitable hole: anyone
  who obtains the anon key can read and write any table directly via the
  REST API, completely bypassing Clerk auth and the FastAPI backend. A
  real instance of this was found and fixed against production on
  2026-08-21 — see `docs/CURRENT_STATE.md`'s "Security fix" entry from
  that date and the Vault note `bugs-solved/supabase-rls-disabled-anon-authenticated-full-crud.md`.
- **This app's own connection role (`postgres`, table owner) has
  `rolbypassrls = true`**, so enabling RLS with zero policies is safe and
  free for this project specifically — it does not break the
  application, only blocks `anon`/`authenticated`. Confirm this is still
  true (`SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user`)
  before assuming "just enable RLS" is a no-op fix on any other database.
- **Every new migration that adds a table must enable RLS on it in the
  same migration** (`op.execute("ALTER TABLE ... ENABLE ROW LEVEL
  SECURITY")` or equivalent) — do not rely on a periodic audit to catch
  a newly-added table with RLS off. As of 2026-08-21, none of this
  project's existing `alembic/versions/*.py` migrations do this
  automatically; RLS was enabled by hand, out of band, after the fact.
  A future ADR/migration should retrofit `create_table` calls (or add a
  dedicated migration) so this is enforced structurally, not by memory.

### Dependencies
- `pip-audit` runs on every CI build and pre-commit hook to catch known CVEs.
- `bandit` runs on every CI build and pre-commit hook to catch common
  security anti-patterns.
