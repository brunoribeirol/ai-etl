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
- `exec()` is only permitted inside `src/ai_etl/core/sandbox.py`, which restricts
  the global namespace to a safe subset of builtins, `pandas`, and `numpy`.
- LLM-generated code is never executed with unrestricted globals.
- Known limitation: restricted `exec()` globals can be bypassed via Python
  introspection (`__class__.__mro__`). This is accepted for the current scope
  with controlled datasets. See inline comment in `sandbox.py`.

### SQL
- SQL queries always use SQLAlchemy bound parameters (`text("... WHERE id = :id")`).
- f-strings in SQL are forbidden — enforced via code review and bandit.

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
