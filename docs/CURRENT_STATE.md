# Current state: AI-ETL

What is true today, on one page. Every dated entry since August 2026 (what changed, what was
verified, why) is in [`history/state-log.md`](history/state-log.md); add a dated entry there at
the end of a working session and update this page. The academic side (report, advising,
evidence) lives in the Obsidian vault, `tcc/README.md`.

**Last updated:** 2026-10-16.

## The system

A multi-agent framework that turns a natural-language specification into an ETL pipeline
(LangGraph graph: Orchestrator, Extractor, Transformer, Quality, Loader) and then answers a
business question over the loaded data (Planner, Analyst, Science, Advisor, optional Reviewer).
Generated code runs in a sandbox; every action of the ETL graph is in the audit log.
Architecture: `CLAUDE.md` and `docs/adr/`.

## Where it runs

| Part | Where | Note |
|---|---|---|
| API, worker, scheduler, Redis | Railway, region Amsterdam (`ams`) | Moving to US East (Virginia) is decided, the owner applies it. Hard spend limit US$ 15 |
| Interface | Vercel, https://ai-etl.vercel.app | Clerk development instance; it fails behind the owner's VPN |
| Database | Supabase project `ai-etl`, `sa-east-1` | Row-level security per tenant |
| Run artefacts, database backups | S3 bucket `ai-etl-artifacts-brlla`, `sa-east-1` | Backups by `.github/workflows/db-backup.yml` |
| Default model | `gpt-4o-mini` (`core/llm.py`, `AI_ETL_LLM_MODEL`) | To be replaced by the comparison of current models |

Health: `GET /health/ready` (database and Redis), never `/health`. Worker logs: Railway service
`tranquil-appreciation`.

## What is verified

| Check | Result | Where |
|---|---|---|
| Case study, 4 scenarios x 5 runs, default model, final code | 20 of 20 completed and equal to the reference | workflow run 38332893793 |
| Scenario 3 across batches | 22 of 30 equal to the reference; the generated code varies there | `history/state-log.md`, 2026-10-13 |
| Connectors without a database behind production (MySQL, MongoDB, SQLite, Parquet) | each completed and equal to the reference | `gh workflow run case-study.yml -f job=connectors` |
| Functional checklist, 57 rows | works 47, in part 2 (2.5 S3 behind an emulator, 3.9 unrelated input), does not work 1 (1.2 PostgreSQL source on production: no database behind the default), not tested 6 (5.4, 5.5, 6.5, 6.7, 8.1, 8.2), note 1 | vault, `tcc/artefact/case-study-evidence-2026-10-12.md`, sections O and P |
| Tests | CI green on `main` (lint, format, types, unit, integration, security, e2e) | `.github/workflows/ci.yml` |

## Open, in the order the advisor fixed on 2026-10-16

1. **Model comparison on current models** (`gpt-6-luna`, `gpt-6.1-sol`,
   `anthropic:claude-haiku-5-5`, `anthropic:claude-sonnet-5-5`; 4 scenarios, 5 runs). Waits for
   the owner to confirm provider credit. Then: default model chosen by the rule fixed
   beforehand (most outputs equal to the reference, then cost per run, then time), old models
   out of the model selector, measured figures on its cards.
2. **Validation rows with the owner:** 8.1, 8.2, 6.5, 6.7 (and 1.2, 2.2 on production) with a
   second account and a disposable database (vault runbook
   `tcc/artefact/runbook-functional-rows-with-the-owner.md`). 5.4 and 5.5 stay untested by
   decision (no notification destination).
3. **Railway region:** move the four services to US East and measure the API before and after
   (2026-10-16, from Amsterdam: `/health/ready` 1.2 to 4.0 s, `/pipelines` 10 s, a run page 12.5 s).
4. **Front-end refinement.** Known: the header navigation overflows at medium widths.
5. **Close the code:** push, message the advisor.

After the report is delivered: set the five Dependabot limits back to 5 (#337), take up the
eight deferred dependency updates (pandas 3 breaks 7 unit tests), replace `railway.json` before
Railway stops reading it on 2026-12-01, decide what happens after the quality gate stops a run.

## Limits kept as they are (for the report's limitations section)

1. The date rule (ISO first, then the interface's locale) exists only as prompt text.
2. Scenario 3 is where generated code varies (22 of 30 equal to the reference).
3. A figure in a narrative is computed; the sentence around it is free text.
4. An input longer than 4 rows that has nothing to do with the question is still analysed.
5. The first line of a failure is the backend's text.
6. A run that fails before the analysis layer stores no cost row.
7. The stored state of a run is not rewritten when the run is approved.
8. The checks added in code are narrow. The decimal-point check (#331, widened in #343 to
   columns with blank values and whole numbers) does not judge values that may use the dot as a
   thousands separator, nor renamed columns; the narrative check does not see a string built in
   another variable. Two false positives are known: a specification that scales dot-decimal
   text by a power of ten, and a narrative that types a defining figure ("the top 10%").
9. A run the quality gate stops ends there for the ETL layer (no repair, no override), and the
   analysis layer still runs on the refused data.
10. The cost estimate before a run (#346) was about half of the real cost for the Telco file
    (US$ 0.0035 estimated; 0.0066 and 0.0089 measured).

## How to work on it

- Git work in `~/dev/ai-etl`; never on `main`: branch, pull request, auto-merge when CI is
  green; then `git pull` in `~/Documents/ai-etl`.
- Local tests on macOS (the analysis-layer tests hang there, see `CLAUDE.md`):
  `uv run pytest tests/unit tests/integration --ignore=tests/unit/test_analyst.py --ignore=tests/unit/test_science.py`.
- Case study: `gh workflow run case-study.yml --ref main -f models=<model> -f runs=5 -f scenarios=1,2,3,4`.
  Scenario 4 runs only on Linux, through that workflow.
- A map of `docs/` is in [`README.md`](README.md).
