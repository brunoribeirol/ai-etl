# Contributing to AI-ETL

## Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (`pip install uv`)
- Docker and Docker Compose (for PostgreSQL in integration tests)
- Make

## Local setup

```bash
git clone https://github.com/brunoribeirol/ai-etl.git
cd ai-etl
cp .env.example .env          # add your OPENAI_API_KEY
make install                  # creates .venv, installs all deps + pre-commit hooks
make db-up                    # starts PostgreSQL via Docker (needed for scenario 2/3)
```

## Running checks

```bash
make lint          # ruff check
make format-check  # ruff format --check
make type-check    # mypy
make test          # pytest unit + integration
make security      # bandit + pip-audit
make check         # all of the above in one command
```

## Branching and commits

- Branch from `main`. Use one of the following prefixes:
  - `feat/` — new functionality
  - `fix/` — bug fixes
  - `chore/` — maintenance (deps, CI, docs)
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  - `feat: add csv source connector`
  - `fix: handle empty DataFrame in quality agent`
  - `chore: update langchain to 0.3.5`
- Keep commits focused — one logical change per commit.
- **Never push directly to `main`**. Open a PR and wait for CI to pass.

## Pull requests

Before opening a PR:
1. `make check` must pass locally (lint + format + typecheck + tests + security).
2. New code must include tests in `tests/unit/` or `tests/integration/`.
3. Changes to agent behavior must be reflected in the vault: `artefact/architecture.md`.
4. Changes to environment variables must be reflected in `.env.example`.

## Adding a new agent

All LangGraph nodes must follow the contract in `src/ai_etl/core/state.py`:

```python
def my_agent_node(state: PipelineState) -> PipelineState:
    if state.get("error"):      # always short-circuit on upstream error
        return state
    # ... logic ...
    new_log = log_action(state, "my_agent", "action_name", {...})
    return {**state, "field": value, "audit_log": new_log}
```

Rules:
- Never mutate state in-place — always return `{**state, ...}`.
- Always call `log_action()` for every significant step.
- Never use `exec()` outside `src/ai_etl/core/sandbox.py`.
- Never use f-strings to build SQL — use SQLAlchemy bound parameters.

## Security

See [SECURITY.md](SECURITY.md) for the full security policy and vulnerability disclosure process.
