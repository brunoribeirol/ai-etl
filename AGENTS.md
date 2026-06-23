# AI-ETL — Agent Instructions

## Project overview

AI-ETL is a multi-agent LLM framework that automates ETL pipelines end-to-end.
Five LangGraph agents (Orchestrator → Extractor → Transformer → Quality → Loader)
share a `PipelineState` TypedDict. The user provides a natural-language spec;
the agents generate auditable Python code and execute it.

## Key files to read before any task

| File | Purpose |
|---|---|
| `src/ai_etl/core/state.py` | `PipelineState` — the central contract between all agents |
| `src/ai_etl/core/graph.py` | LangGraph topology and routing logic |
| `src/ai_etl/agents/<agent>.py` | The relevant agent node |
| `~/Documents/Obsidian Vault/tcc/artefact/architecture.md` | Full spec: prompts, flow, error handling per agent |

## Non-negotiable rules

**Never:**
- Use f-strings to build SQL queries — use SQLAlchemy bound parameters
- Call `exec()` outside `src/ai_etl/core/sandbox.py`
- Commit `.env` or log API keys
- Mutate `PipelineState` in-place — always return `{**state, "field": value}`
- Break the node signature: every node must be `(state: PipelineState) -> PipelineState`

**Always:**
- Call `log_action()` from `src/ai_etl/audit/logger.py` for every agent action
- Short-circuit on upstream errors: `if state.get("error"): return state`
- Add type hints and run `make type-check` before proposing changes
- Write tests for new modules in `tests/unit/` or `tests/integration/`

## Commands

```bash
make install       # uv sync --all-extras + pre-commit hooks
make check         # lint + format-check + typecheck + tests + security
make format        # auto-fix formatting
make security      # bandit + pip-audit
make db-up         # start PostgreSQL for integration tests
make run-scenario1 # run case study scenario 1 end-to-end
```

## Architecture in one sentence

Five LangGraph nodes share a `PipelineState` TypedDict. Each node reads from
the state, executes, and returns the updated state. The Audit module records
all actions. The Quality node gates the Loader via conditional routing.
