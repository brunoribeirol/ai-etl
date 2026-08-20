# AI-ETL

[![CI](https://github.com/brunoribeirol/ai-etl/actions/workflows/ci.yml/badge.svg)](https://github.com/brunoribeirol/ai-etl/actions/workflows/ci.yml)
[![Frontend CI](https://github.com/brunoribeirol/ai-etl/actions/workflows/frontend-ci.yml/badge.svg)](https://github.com/brunoribeirol/ai-etl/actions/workflows/frontend-ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Multi-agent framework for automated ETL pipelines using LLMs.

Describe your data pipeline in plain English. Five specialized agents — Orchestrator, Extractor, Transformer, Quality, and Loader — execute it end-to-end over CSV, PostgreSQL, REST, and PDF/DOCX sources, generating auditable Python code at every step. A second layer — Planner, Analyst (Gold), Science, Advisor — turns the cleaned data into business-question answers, charts, and prescriptive recommendations.

> **Academic context:** Bachelor's thesis (TCC) at CESAR School, 2026.
> Methodology: Design Science Research (Hevner et al., 2004).
> **Full project knowledge base:** `~/Documents/Obsidian Vault/tcc/` — research, decisions, requirements, and technical specs live there. `docs/CURRENT_STATE.md` is this repo's own living state doc.

---

## Project structure

This is a monorepo with two independently deployed halves:

```
ai-etl/
├── src/ai_etl/          # Python backend — agents, pipeline, API (deploys to Railway)
│   ├── agents/          #   Orchestrator/Extractor/Transformer/Quality/Loader +
│   │                    #   Planner/Analyst/Science/Advisor (LangGraph nodes)
│   ├── core/            #   PipelineState, LangGraph wiring, sandboxed code exec, LLM client
│   ├── sources/          #   csv/postgres/sqlite/mysql/mongodb/rest(+auth)/document connectors
│   ├── destinations/    #   csv/postgres writers
│   ├── audit/            #   Postgres audit trail (runs/analysis_runs) + storage.py (local/S3)
│   ├── services/          #   pipeline orchestration, Celery async queue, auth, spec builder
│   └── api/               #   FastAPI HTTP layer for the frontend (ADR-011)
├── tests/{unit,integration,e2e}/
├── alembic/               # Postgres migrations for the audit-trail database
├── docs/{adr,work}/       # Architecture Decision Records + implementation plans
├── case_study/            # TCC evaluation datasets, pipeline specs, baselines
│
└── frontend/              # Next.js + Clerk — the real login/UI (deploys to Vercel)
    └── src/{app,components,middleware.ts}
```

Backend and frontend are versioned together (same PR history, same commit range) but deploy and scale independently — Railway never builds `frontend/`, Vercel never builds anything outside it (`frontend-ci.yml`/Vercel's own build are both scoped to that directory; `ci.yml` never touches it). See `docs/adr/ADR-011-nextjs-frontend-fastapi-clerk-middleware.md` for why.

Physically separating the Python backend into its own `backend/` directory (mirroring `frontend/`) is a planned follow-up once the frontend cutover (Sprint 6) is fully live — deferred for now to avoid reconfiguring Railway's deploy root mid-sprint.

---

## Architecture

```
[Natural language spec]
        ↓
  Orchestrator  →  Extractor  →  Transformer  →  Quality  ──(ok)──→  Loader
                                                          └─(error)──→  END
                                                              ↓ (if a business question was asked)
                                            Planner  →  Analyst (Gold) / Science  →  Advisor
```

Every agent shares a `PipelineState` TypedDict — no agent communicates directly with another, all information flows through state. Every action is logged to an auditable trail (Postgres `runs`/`analysis_runs`, plus JSON/CSV/figure artifacts via `audit/storage.py`, local disk by default or S3 when `STORAGE_BACKEND=s3` — ADR-009).

Auth is Clerk (JWT/JWKS, verified server-side, ADR-006), verified by the API (`src/ai_etl/api/`) via `services/auth_service.verify_session_token()`.

---

## Quick start (backend)

```bash
git clone https://github.com/brunoribeirol/ai-etl
cd ai-etl
cp .env.example .env        # fill in OPENAI_API_KEY, CLERK_* (see docs/adr/ADR-006)
uv sync --all-extras
make db-up                  # PostgreSQL (optional, for scenarios 2-3)
make app-db-up               # application database (audit trail)
make db-migrate
make redis-up                # Redis (Celery broker/backend, ADR-008)

make api                     # FastAPI, http://localhost:8000 (Sprint 6, ADR-011)
make celery-worker           # async pipeline execution worker

# Or headless, no auth/UI:
python -m ai_etl run --spec "Read sales.csv, rename dt to date, filter active rows, save as output.csv"
```

## Quick start (frontend)

```bash
cd frontend
cp .env.example .env.local   # fill in NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
npm install
npm run dev                  # http://localhost:3000 — run `make api` alongside it
```

See `frontend/README.md` for details.

---

## Case study scenarios

| Scenario | Sources | Destination | Description |
|---|---|---|---|
| 1 | CSV | CSV | Rename + filter + dedup |
| 2 | CSV + PostgreSQL | PostgreSQL | Join + clean |
| 3 | CSV + PostgreSQL + REST API | PostgreSQL | Full heterogeneous pipeline |
| 4 | CSV + PostgreSQL + REST API + PDF/DOCX | PostgreSQL | + LLM-structured document extraction (ADR-010) |

```bash
make run-scenario1
make run-scenario2
make run-scenario3
```

`tests/e2e/` runs all 4 scenarios against the real stack (Postgres, Celery, sandbox, auth) in CI — see `make test-e2e`.

---

## Development

```bash
make install     # install dependencies with uv
make test        # unit + integration tests
make test-e2e     # the 4 case-study scenarios, full stack (needs Postgres/Redis — see Makefile)
make check        # lint + format + type-check + test + test-e2e + security
```

`frontend/`'s own checks (`npm run lint`, `npm run build`) run in `frontend-ci.yml`, not `make check`.

---

## Security notes

- LLM-generated Transformer/Analyst/Science code runs inside `core/sandbox.py`'s isolated `multiprocessing.Process` (spawn context), with a real enforced timeout and no network/file-system access beyond what's explicitly passed in — see [ADR-007](docs/adr/ADR-007-unified-sandbox-policy.md) (supersedes the earlier single-`exec()` design in ADR-003).
- Auth: Clerk JWT verified server-side via JWKS (`iss`/`exp`/`sub` all required, fails closed) — [ADR-006](docs/adr/ADR-006-clerk-auth-supabase-postgres-tenancy.md).
- Every `runs`/`analysis_runs` row and S3 storage key is tenant-scoped; cross-tenant access is checked server-side, not just filtered in the UI.
- API keys are never logged — the audit logger redacts sensitive fields automatically.
- SQL queries always use SQLAlchemy parameterized execution, table/column names validated against an allowlist regex before ever reaching a query.

See `SECURITY.md` and `docs/adr/` for the full risk analysis per decision.

---

## License

MIT
