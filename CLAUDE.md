# CLAUDE.md — AI-ETL Framework

## What this project is

AI-ETL is a multi-agent framework based on LLMs to automate end-to-end ETL pipelines.
The user provides a natural language specification; 5 specialized agents orchestrated
via LangGraph perform extraction, transformation, quality, and loading, generating auditable Python code.

**Context:** Computer Science capstone project (TCC) — CESAR School, 2026.

**Canonical source of context (decisions, requirements, research):**
`~/Documents/Obsidian Vault/tcc/`

---

## Before any task — read in this order

| What changed? | What to read first |
|---|---|
| An agent | `src/ai_etl/agents/<name>.py` → vault: `artefact/architecture.md` |
| Pipeline state | `src/ai_etl/core/state.py` |
| Graph topology | `src/ai_etl/core/graph.py` |
| Source or destination | `src/ai_etl/sources/` or `destinations/` |
| Audit/logging | `src/ai_etl/audit/logger.py` + `src/ai_etl/audit/db.py` |
| Execution sandbox | `src/ai_etl/core/sandbox.py` + `docs/adr/ADR-003-exec-sandbox.md` |
| Architecture decision | `docs/adr/` → vault: `artefact/decisions.md` |
| General TCC context | vault: `CONTEXT.md` |

**Canonical source — Obsidian vault (`~/Documents/Obsidian Vault/tcc/`):**
- `artefact/architecture.md` — authoritative technical spec of the 5 agents
- `artefact/decisions.md` — why the choices were made
- `artefact/requirements.md` — functional/non-functional requirements per agent
- `artefact/case-study.md` — protocol for the 3 scenarios
- `artefact/security.md` — risks and mitigations
- `artefact/testing.md` — testing strategy
- `CONTEXT.md` — overall TCC state and next steps

> Files under `docs/adr/` are the exception: ADRs live in the repo because they are implementation decisions, not research documentation.

---

## Available commands

```bash
make install       # uv sync --all-extras + installs the package in editable mode
make test          # pytest unit + integration, cov >80%
make lint          # ruff check
make format        # ruff format + fix
make format-check  # ruff format --check (CI)
make type-check    # mypy src/
make security      # bandit + pip-audit
make check         # all of the above in sequence
make db-up         # PostgreSQL via docker-compose
make run-scenario1 # runs scenario 1 of the case study
make run-scenario2
make run-scenario3
```

---

## Non-negotiable rules (violation = immediate revert)

**Never do:**
- f-strings for SQL queries — use SQLAlchemy bound parameters (`text("... WHERE id = :id")`)
- `exec()` outside `src/ai_etl/core/sandbox.py`
- Commit `.env` — use only `.env.example`
- API keys in logs — the logger already auto-redacts "key", "token", "secret" fields
- Break the LangGraph node signature — every node: `(state: PipelineState) -> PipelineState`
- Mutate state in-place — always `{**state, "field": new_value}`
- Commit directly to `main`

**Always do:**
- Use `log_action()` from `src/ai_etl/audit/logger.py` for every relevant action of every agent
- Type hints on all code — run `make type-check` before committing
- Write tests for new modules in `tests/unit/` or `tests/integration/`
- Run `make check` before opening any PR

---

## SR Big Tech standard — applied automatically in every session

**This standard applies to ALL work in this project, without exception.**
Full spec: `.claude/specs/sr-standard.md`
Execution checklist: `.claude/skills/sr-quality-check.md` (invoke via `/sr-quality-check`)

### Before marking any task as complete

```bash
make check  # lint + format-check + type-check + test + security — must pass 100%
```

Project-specific checklist:
- Every LangGraph node: signature `(state: PipelineState) -> PipelineState`, returns `{**state, ...}`
- `log_action()` called for every relevant action of every agent
- Short-circuit `if state.get("error"): return state` in every node
- `exec()` only in `core/sandbox.py`
- SQL only via SQLAlchemy `text()` with parameters — never f-strings
- `sqlite3.connect()` always wrapped with `contextlib.closing()`
- No `print()` in production — use `log_action()`
- Tests covering: happy path + short-circuit + audit log + error
- Coverage ≥ 80% maintained
- No new `# type: ignore` without a comment explaining why

### Critical anti-patterns for this project

| Anti-pattern | Consequence |
|---|---|
| `state["field"] = value` | In-place mutation — breaks the LangGraph contract |
| `exec()` outside the sandbox | No builtins restriction — security risk |
| `conn = sqlite3.connect(); ...; conn.close()` | Connection leak if an exception occurs |
| `f"SELECT * FROM {table}"` directly | SQL injection |
| `query` from `load_postgres()` coming from user input | SQL injection in SaaS |
| `# type: ignore` without a comment | Invisible technical debt |
| Integration tests that duplicate unit tests | Doesn't test real integration |

### Mandatory Git conventions from now on

- Branch: `feat/<name>`, `fix/<name>`, `chore/<name>`, `docs/<name>`, `test/<name>`
- Commits: Conventional Commits in English (`feat: ...`, `fix: ...`, `test: ...`)
- Never commit directly to `main`
- Tags: Semantic Versioning `vMAJOR.MINOR.PATCH` at every milestone

---

## Available skills

- `.claude/skills/add-agent.md` — full checklist for adding a new LangGraph agent
- `.claude/skills/run-pipeline.md` — how to run and verify a case study scenario
- `.claude/skills/sr-quality-check.md` — SR Big Tech audit before any delivery

---

## Architecture in one sentence

```
[spec]                                              (Silver — ETL pipeline, LangGraph graph)
  → Orchestrator (LLM, JSON plan)
  → Extractor (deterministic, CSV/PG/REST/... → DataFrame + schema)
  → Transformer (LLM → Python code → sandbox exec → DataFrame)
  → Quality (deterministic, nulls + duplicates + outliers → severity)
  → Loader (deterministic, DataFrame → CSV/PG/S3)
     └─ if severity == "error" → END (pipeline blocked)

[business question]                       (Agentic BI — analysis layer, outside the graph)
  → Planner (LLM, decomposes the question into descriptive/analytical sub-analyses)
  → Analyst/Science (LLM → code → sandbox, one call per sub-analysis
    + auto-repair; Reviewer does an opt-in second pass per result — ADR-037)
  → Advisor (LLM, synthesizes Gold/Science into prescriptive recommendations)
```

All shared state via the `PipelineState` TypedDict in `src/ai_etl/core/state.py`.
Every action logged via `log_action()` → persisted to JSON + SQLite by `save_run()`.
End-to-end orchestration (Silver → Planner → Analyst/Science → Advisor) in
`src/ai_etl/services/pipeline_service.py::run_full_analysis`.

---

## Stack

```
Python 3.11+  |  langgraph>=0.2  |  langchain>=0.3  |  langchain-openai>=0.3  |  openai>=1.50
pandas>=2.0   |  sqlalchemy>=2.0 |  httpx>=0.27     |  python-dotenv
```

Dev: `ruff` | `mypy` (strict, 3.12) | `bandit` | `pip-audit` | `pytest-cov` | `pre-commit`

---

## Environment variables

```bash
OPENAI_API_KEY=sk-...
AI_ETL_LLM_MODEL=gpt-4o-mini   # gpt-4o for the final case study
POSTGRES_URL=postgresql://ai_etl:ai_etl@localhost:5432/ai_etl_db
```

---

## Folder structure

```
src/ai_etl/
├── agents/
│   ├── pipeline/    # orchestrator, extractor, transformer, quality, loader (Silver, LangGraph graph)
│   └── analysis/    # planner, analyst, science, advisor, reviewer (Agentic BI, outside the graph)
├── api/             # FastAPI: main.py, deps.py, config.py, serialization.py
│   └── routers/     # pipelines, runs, admin, budget, cost_estimation, llm, onboarding, secrets, tenant
├── services/        # orchestration layer: pipeline_service.py (run_full_analysis),
│                     # execution_queue, scheduler, auth/secrets/tenant services, alerting, digest
├── core/            # state.py, graph.py, sandbox.py, llm.py, pricing.py, drift.py, scheduling.py, ...
├── sources/         # csv, postgres, mysql, mongodb, rest, sqlite, document
├── destinations/    # csv_dest, postgres_dest, s3_parquet_dest
└── audit/           # logger.py, models.py, storage.py, connection.py, admin_log.py
    └── db/          # budget, health, locale, onboarding, pipelines, retention, runs

tests/
├── unit/           # no external I/O — mocker for LLM and sources
├── integration/    # agents with LLM mocks, real sources
└── e2e/            # 3 full scenarios

docs/
├── architecture.md
├── adr/            # ADR-001 to ADR-004 (and future ones)
└── case-study.md

case_study/
├── pipelines/      # scenario1_spec.txt, scenario2_spec.txt, scenario3_spec.txt
├── data/           # datasets (gitignored)
└── results/        # run JSONs (scenario1/, 2/, 3/)
```
