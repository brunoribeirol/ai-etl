# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [1.0.0] - 2026-08-22

First tagged release since `v0.1.0`. Marks the state of `main` after the original
28-sprint roadmap plus the public landing page, and establishes real release hygiene
going forward (branch protection requiring review, CODEOWNERS, PR/issue templates,
a tag-driven `release.yml`).

This entry summarizes Sprints 15–29 and the landing page — the deliveries that
happened without a corresponding CHANGELOG update. Sprints 2–14 (frontend rebuild
on Next.js/shadcn, Railway/Vercel deploy cutover, additional sources, AWS
multi-cloud proof, scale hardening at 200k rows) also shipped in this window but
predate this changelog discipline; see `git log` and `docs/adr/` for that detail.

### Added

- **Sprint 15 — reliability**: retry policy and failure alerting for scheduled pipeline runs
- **Sprint 16 — configurable quality rules (ADR-023)**: operators define declarative,
  whitelisted per-pipeline quality rules (`not_null`, `gte`, `lte`, `gt`, `lt`, `eq`, `ne`),
  evaluated alongside the fixed null/duplicate/outlier checks — no `exec()`/`eval()` of
  user input
- **Sprint 17 — run history (ADR-017)**: comparable run history for saved pipelines
- **Sprint 18 — executive summary UI**: frontend summary view for saved pipeline runs
- **Sprint 19 — RBAC (ADR-021)**: role-based access via Clerk Organizations, org SSO
  prerequisite, tenant-scoped secrets
- **Sprint 20 — S3-Parquet destination**: new warehouse destination connector
- **Sprint 21 — output sanity-check (ADR-026)**: guardrails on Gold/Science agent results
  before they reach the user
- **Sprint 22 — hardened `csv_source`**: resilient to real-world dirty CSV input
- **Sprint 23 — multi-provider LLM (ADR-012)**: Anthropic, Google, and local Ollama
  support behind `AI_ETL_LLM_PROVIDER`, defaulting to the existing OpenAI behavior
- **Sprint 24 — compliance**: SOC2 readiness self-assessment, LGPD/GDPR data record,
  tenant data deletion flow
- **Sprint 26 — onboarding**: self-serve activation flow for new tenants
- **Sprint 27 — human approval gate (ADR-028)**: dry-run / approval step before
  production loader writes
- **Sprint 28 — regression harness (ADR-029)**: automated prompt/agent regression testing
- **Sprint 29 — tenant budget cap (ADR-017)**: monthly budget cap enforced pre-enqueue
- **Landing page (ADR-030)**: public marketing page at `/`
- **GitHub release hygiene (Sprint 32)**: branch protection now requires 1 approving
  review on `main`, `CODEOWNERS`, PR/issue templates, and a real `v1.0.0` tag to
  exercise `release.yml` for the first time

[1.0.0]: https://github.com/brunoribeirol/ai-etl/compare/v0.1.0...v1.0.0

---

## [0.1.0] - 2026-06-22

### Added

- **5-agent LangGraph architecture**: Orchestrator, Extractor, Transformer, Quality, Loader nodes sharing a `PipelineState` TypedDict
- **PipelineState**: central immutable contract passed between all agents; includes `spec`, `run_id`, `pipeline_plan`, `extracted_data`, `source_schemas`, `transformation_code`, `transformed_data`, `quality_report`, `load_result`, `audit_log`, `error`, `status`
- **Orchestrator agent**: LLM-based pipeline planning with JSON output, 2-retry loop on parse failures
- **Extractor agent**: deterministic multi-source extraction (CSV, PostgreSQL, REST API) with schema inference
- **Transformer agent**: LLM code generation + sandboxed `exec()` execution, 3-attempt retry loop
- **Quality agent**: deterministic checks — null ratios, duplicates, IQR outliers; severity: ok / warning / error
- **Loader agent**: deterministic multi-destination loading (CSV, PostgreSQL) with rows_loaded tracking
- **Audit trail**: every action logged via `log_action()` + persisted to JSON and SQLite via `save_run()`
- **Sandbox**: restricted `exec()` in `core/sandbox.py` — blocks file I/O and network access
- **Sources**: `csv_source`, `postgres_source` (with table-name validation), `rest_source`
- **Destinations**: `csv_dest`, `postgres_dest` (with table-name validation)
- **SQL injection prevention**: `_validate_table_name()` regex guard in both postgres connectors
- **Test suite**: 80 unit tests, 91.8% coverage (threshold: 80%)
- **CI pipeline**: GitHub Actions with Python 3.11/3.12 matrix, lint + format + mypy + tests + bandit + pip-audit
- **Pre-commit hooks**: ruff, mypy, bandit, pip-audit on every commit
- **Docker support**: `Dockerfile` with uv-based build
- **Case study dataset**: 5000-row `sales.csv` with injected quality issues (seed=42)
- **GitHub automation**: Dependabot (pip + GitHub Actions), issue templates, release workflow, CODEOWNERS, PR template
- **Documentation**: `CONTRIBUTING.md`, `SECURITY.md`, `AGENTS.md`, `docs/architecture.md`
- **ADRs**: architecture decision records in `docs/adr/`

[Unreleased]: https://github.com/brunoribeirol/ai-etl/compare/v1.0.0...HEAD
[0.1.0]: https://github.com/brunoribeirol/ai-etl/releases/tag/v0.1.0
