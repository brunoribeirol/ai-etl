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

### Dependencies
- `pip-audit` runs on every CI build and pre-commit hook to catch known CVEs.
- `bandit` runs on every CI build and pre-commit hook to catch common
  security anti-patterns.
