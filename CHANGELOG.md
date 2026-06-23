# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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

[Unreleased]: https://github.com/brunoribeirol/ai-etl/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/brunoribeirol/ai-etl/releases/tag/v0.1.0
