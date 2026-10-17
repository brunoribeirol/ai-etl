# Full technical + product audit — Sr/Staff Big Tech bar, pre-launch

## Objective
Real, execution-based audit of the whole AI-ETL project (not a code-reading review) across
13 personas (grouped into 12 via one merge), to produce a single consolidated report:
what's genuinely verified working, what's broken, and a prioritized punch-list before
calling this "10/10 Sr/Staff Big Tech ready."

## Non-goals
No fixes this round. No new features. No permanent changes to `main` or to any production
system beyond read-only verification calls (a live HTTP GET, a `railway logs` read, etc.).

## Assumptions confirmed before dispatch
- `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` are live in Railway (`ai-etl` service) — reachable via
  `railway run` without ever exposing the values to an agent's context.
- `UV_PROJECT_ENVIRONMENT=/tmp/ai-etl-venv-<slug>` avoids the documented iCloud `uv sync`
  hang (`bugs-solved/mypy-pytest-hang-agent-sandbox.md`) — each agent uses its own slug to
  avoid a shared-venv collision when running in parallel.
- Railway CLI's project link is path-keyed (`~/.railway/config.json`), not global — any
  agent needing `railway run` must first run, from its own worktree:
  `railway link -p 637ee772-a5c2-4dd7-b38b-f35d1665a2e9 -e c2f1ad6e-3d51-4f5b-8e99-c4b38a0ecdc2 -s 4a777b36-1e64-49b2-aef1-aca25df90336`
- Production URLs: API `https://ai-etl-production.up.railway.app`, frontend
  `https://ai-etl.vercel.app`.
- Real known findings from this and prior sessions the personas should confirm still hold
  (not re-litigate from scratch): PR #109's 5 bugs, PR #113's ADR-037 review (now live),
  the 2026-08-22 audit's Sprint 30-38 hardening, the exec() sandbox introspection-bypass
  limitation (accepted, documented, not fixed), RLS enabled + Data API now disabled.

## Batching (max 3 concurrent, worktree-isolated, per this repo's own documented
git-object-store-corruption-with-parallel-worktrees history)

**Batch 1 — strategy & product** (mostly real product-flow testing + document review):
CTO+Founder (merged — both are "would this survive scrutiny/pass diligence"), Product/PO,
Academic evaluator (banca de TCC).

**Batch 2 — engineering depth** (real execution required): Tech Lead/Solutions Architect,
Data Engineer (real dirty CSV/PDF/DOCX ingestion), LLM/Prompt Engineer (real 9-agent runs
against real credentials).

**Batch 3 — security & ops** (real attack attempts + real ops checks): Red Team, Blue Team,
SRE/DevOps.

**Batch 4 — QA & UX** (real test runs + real browser automation): QA/Test Automation,
non-technical end user (via `claude-in-chrome`), Accessibility (via `claude-in-chrome`).

## Verdict framework every agent must use per item
"✅ funciona de verdade, verificado" (with the exact command/output/evidence) vs.
"⚠️ parece pronto, nunca testado" (code exists, never exercised for real) vs.
"❌ gap real" (confirmed broken/missing, with `path:line` or reproduction steps).
Never inflate — a claim without a real command/output/URL behind it is a ⚠️, not a ✅.

## Validation
Each agent reports back in-conversation (structured findings, not a file) unless a report
file is more useful for length — orchestrator (me) synthesizes into the final consolidated
report after all 4 batches complete.

## Consolidated findings (all 12 personas complete, 2026-08-24)

See the full session transcript for each persona's complete report. Summary and
cross-validated findings synthesized by the orchestrator below — not re-typed here in
full; this file exists as the plan-of-record, the real deliverable is the chat response.

**Critical, cross-cutting findings that appeared independently from multiple personas**
(higher confidence because two unrelated audits found the same thing):
- `tests/integration/` confirmed broken by both SRE and QA independently, same root cause.
- The Advisor/Science hedging + LLM-review blind spot found by the LLM/Prompt persona via
  code + real run was independently CONFIRMED LIVE IN PRODUCTION by the non-technical-user
  persona (Sudeste/Norte contradiction, English warning ignored by Advisor).
- Model-picker missing from the one-off "Executar" flow, found independently by both the
  orchestrator (pre-audit) and the Product/PO persona live in the browser.

**New, previously-unknown findings from this audit**:
- CRITICAL: unvalidated `query` field on sqlite/mysql sources reachable from LLM-generated
  `pipeline_plan` — real SQL injection / destructive DDL, actually exploited by Red Team.
- HIGH: `document_source.py::_extract_docx_text` never reads `document.tables` — silent
  total data loss on any real Word table, found by Data Engineer via real file creation.
- HIGH: Railway `ai-etl` service has no `healthcheckPath` — a broken deploy would still
  promote to serving traffic, found by SRE.
- MEDIUM: `audit/logger.py::_sanitize` doesn't recurse into nested dicts and misses
  "authorization"/"bearer"-shaped keys — real secret-leak vector into persisted audit logs,
  found by Blue Team.
- MEDIUM: "ADR-016" (internal architecture-decision jargon) leaked into production UI text,
  found live by the non-technical-user persona.
- Full punch-list, verdicts, and evidence: see chat transcript for each persona's complete
  structured report (CTO/Founder, Product/PO, Academic evaluator, Tech Lead, Data Engineer,
  LLM/Prompt Engineer, Red Team, Blue Team, SRE, QA, Non-technical user, Accessibility).

---

## Full per-persona reports

### Batch 1 — Strategy & Product

#### CTO + Founder (combined)

**Genuinely strong (evidence)**: API live and healthy (`/health`→200, `/docs`→200). PR/sprint narrative in `CURRENT_STATE.md` matches real `git log`/`gh pr list`. Real (not mocked) case-study data in `case_study/results/model_comparison_2026-08-23_sonnet_only/` (3/3 real `claude-sonnet-5` runs, real cost). 37 ADRs with explicit trade-offs, gaps flagged not hidden. SQL/`exec()` non-negotiable rules genuinely followed in the code checked.

**Red flags (evidence)**:
- CRITICAL — `security.md` says exec()→Docker is "CRÍTICA antes do beta"; never done. ADR-032 formally accepts the introspection-bypass risk instead. Real gap between written promise and shipped state, with real customers/keys behind it.
- Clerk still in development mode in production (`pk_test_...` keys) — real DX/session limits apply, not cosmetic.
- Bus factor = 1 (`git log --format='%an' | sort | uniq -c` — 100% one author + dependabot).
- Zero paying customers, zero billing system (confirmed in the LGPD doc's own "billing (future)" note).
- Cost-per-run is well measured (`core/pricing.py`, real data) but no customer-facing price list exists anywhere — cost without margin/pricing strategy.
- Per-pipeline LLM provider override took several sprints to actually reach execution (PR #104-class gap) — the kind of thing that breaks a live "pick your model" demo if hit at the wrong moment (now closed, per later ADR-037 work).
- Heavy vendor lock-in (Railway/Vercel/Clerk/Supabase/OpenAI/Anthropic/Google) with no exit plan beyond AWS S3 portability for storage specifically.
- SOC2 self-assessment (`docs/compliance/soc2-readiness-assessment.md`) is honest but lists real P0/P1 gaps: no centralized error tracking verified end-to-end, no incident-response plan, no tested backup/restore, no sub-processor registry.
- Dual ICP is a product decision, not yet validated by any real paying user on either side.

**Punch-list**: Alta — fix/rearchitect the sandbox story before any due-diligence conversation; get Clerk out of dev mode before any external demo; get at least one real pilot user; define customer pricing. Média — bus-factor mitigation/handoff docs; real error tracking + incident-response plan; tested backup/restore. Baixa — reduce/document vendor lock-in exposure; consolidate the 3 `exec()` call sites.

#### Product/PO

**Genuinely good, verified live**: landing page is coherent and honest (no fabricated metrics). Dual ICP is real in the rendered UI (`/app`,`/pipelines`,`/historico` technical; `/resumo` plain-language), not just aspirational. `ModelPicker` works for real in a saved pipeline's edit form (real cost/reliability data, 4 selectable models). `/resumo` gives a genuine friendly empty state, no raw `String(err)`. `/historico` shows real run history (8 real runs, real costs, real failure row). `/comecar` checklist reflects real completed usage. i18n: 195/195 keys present both languages, zero missing.

**Real friction confirmed live, prioritized**:
- Alta — Model selection invisible on the primary "Executar" one-off flow (`executar-form.tsx` never imports `ModelPicker`); this is the single biggest gap between "looks done" and "works for a first-time customer."
- Alta — Cross-referenced all 37 OpenAPI routes against every frontend fetch call: `secrets`, `budget`, `runs/pending-approval`+`approve`/`reject`, `runs/estimate`, `llm/test-connectivity`+`allowed-models` (picker uses a static file instead), per-pipeline notification config, `tenant/export`+`retention`, and the entire `admin/*` surface have **zero** frontend consumer.
- Média — saved-pipeline quality rules require hand-written raw JSON in a textarea, no builder UI (defensible for the "operador técnico" persona, but a real rough edge). Landing-page scroll-reveal animation lags 1-3s at normal scroll speed.
- Baixa — minor timezone display oddity in `/historico`.
- Not independently verifiable: the unauthenticated Clerk sign-up gate itself (browser already carried an authenticated session).

#### Academic evaluator (banca CESAR School)

**Real verification performed**: re-ran `pytest tests/unit --collect-only`/full run → 930 passed, matches `CURRENT_STATE.md` exactly, not an inflated/copied number. Cross-checked `requirements.md`'s own "Critérios de Avaliação" table against real case-study data (`tabela-resultados.md`, `sprint8/stability_summary.json`, `model_comparison_2026-08-23_sonnet_only/`) — all criteria met with real data, with margin.

**✅ Holds up**: the "honest about limitations" pattern (ADR-003/006/031/032/037 all explicitly flag what's not done) is intact from v0 through Sprint 37. Architecture-decision justifications (LangGraph vs. CrewAI, `exec()` sandbox trade-off, Quality-blocks-Load routing) would survive rigorous committee questioning — real, concrete criteria, not post-hoc rationalization.

**⚠️/❌ Real risks for a defense**:
- The TCC's own writing drafts (`writing/drafts/draft-product-vision.md` §8) present the Agentic BI layer (Planner/Analyst/Science/Advisor) as *future/speculative* — but it's fully built and running in production today (Sprints 21+, `agents/analysis/`). A committee member checking the real GitHub (37 ADRs, `v1.0.0` tag) could reasonably ask "what exactly is being defended here?" **Real, fixable-before-defense discrepancy.**
- No draft explicitly delimits "this is what's being defended as the TCC (v0)" vs. "this is post-TCC product work" (37 ADRs, RBAC, billing, i18n) — `decisions.md` itself only notes this split internally in the vault, never in the actual writing drafts.
- Sprint 28's regression-harness baseline is still `data_source: "mock"` — the harness's real prompt-regression-catching value is unverified against a real model in the committed baseline.
- Evaluation-metrics framework (`evaluation-metrics.md`) has only 3 of its 10 metrics actually instrumented today (Nov/26 defense timeline risk, if the evaluation section depends on all 10).
- DSR is cited as a theoretical reference in `evaluation-metrics.md` while `decisions.md` is explicit DSR was rejected as the *formal* methodological classification — needs precise wording in the actual Methodology section to avoid an apparent self-contradiction if questioned directly.

**Reproducibility**: a third party could reproduce the deterministic parts (`make check`, `pytest`, `case_study/scripts/*`) with zero cost; the LLM-dependent parts need real API keys (correctly not committed) — expected, documented, not a rigor gap.

**Punch-list**: Crítico — delimit v0-vs-SaaS scope explicitly in the actual TCC text; fix `draft-product-vision.md` §8's framing. Alto — instrument the remaining 7/10 evaluation metrics before writing the Avaliação section. Médio — run the regression harness once with real credentials for a non-mock baseline. Baixo — make the DSR-as-reference-not-classification distinction explicit in the Methodology section.

### Batch 2 — Engineering depth

#### Tech Lead / Solutions Architect

**✅ Verified live**: `ruff`/`mypy` (89 files)/`bandit` (9815 LOC) all clean; `pytest tests/unit` 930 passed, 94% coverage. Zero circular `core→api` imports (`grep`, zero hits). `agents/{pipeline,analysis}` split genuinely clean in source (stale references only in immutable historical ADR files, which is correct). No stray "N"-suffix duplicate files found in this checkout.

**❌ Real findings (path:line)**:
- `CLAUDE.md`'s own "Estrutura de pastas"/"Arquitetura em uma frase" sections are stale — omit `agents/{pipeline,analysis}`, `api/`, `services/`, `audit/db/{...}`, most real source connectors, and the entire Agentic BI layer. The project's own governing living doc has drifted, not just the vault's historical design doc.
- `pipeline_service.py:444-573` — `run_gold_with_repair`/`run_science_with_repair` are near-verbatim duplicated control flow; a generic `_run_with_repair()` helper would collapse ~60 lines.
- `pipeline_service.py:902-903,938-939` — bare `except Exception: pass` around `record_pipeline_health`/`mark_pipeline_approved` with zero logging, inconsistent with the file's own earlier `logger.warning(exc_info=True)` pattern elsewhere.
- `audit/db/runs.py:619` — redundant local `import pandas as pd` (already imported at module top).
- ADR-031's residual gap confirmed still open in code: `_write_analysis_row` (`audit/db/runs.py:361`) prices cost via the deployment-global model, not the resolved per-pipeline override.
- `core/paths.py`/`core/analysis_types.py` have zero dedicated test files (low risk, but a real gap by the audit's own bar).
- `__main__.py:29,35,38` uses `print()` against the project's own "no print() in production" rule (defensible as CLI UX, but a strict reviewer would flag it).

**Punch-list**: Alta — none (the gates being clean is itself the standout finding). Média — the 4 items above (repair-function dedup, silent-exception logging, `CLAUDE.md` staleness, ADR-031 cost-tracking gap). Baixa — redundant import, missing tests for 2 trivial modules, `__main__.py print()`.

#### Data Engineer

**✅ Verified live**: all 3 documented dirty-CSV bugs (encoding, delimiter, malformed quoting) still fixed, re-confirmed against all 9 existing fixtures. 5 brand-new test cases (BOM, blank lines, empty column, merged-cell multi-row Excel header, huge-but-valid cell) run for real — 4/5 handled correctly. PDF ingestion end-to-end with a real generated PDF + real LLM credentials → correct 4×4 structured DataFrame. No f-string-built SQL anywhere in `sources/`/`destinations/` (grep-verified — the only f-strings found are post-`validate_table_name()` table names, not user-value interpolation). `scale_timeout_for_rows()` re-verified with a real sandboxed sleep-based script at 3 real row-count tiers, matches ADR-013 exactly.

**❌ Real findings**:
- **CRITICAL, never previously found**: `document_source.py::_extract_docx_text` only reads `document.paragraphs`, never `document.tables`. A real `.docx` created with a heading + a real 4-row/4-column table → `_extract_text` returned only 34 characters (the heading), the whole table vanished. The real LLM call then received text with zero data and returned `[]` → `load_document()` returned an **empty (0,0) DataFrame with no exception, no error at all** — total silent data loss on the single most common way tabular data appears in a real Word document. Confirmed never exercised: `test_document_source.py` mocks `_extract_docx_text` entirely; the one e2e DOCX test only uses `add_paragraph()`, never a real table.
- `csv_source.py:220-224` — a genuinely well-formed CSV (verified via a pure `csv.reader` pass) but with one legitimate field >128KB is rejected with a misleading `"Malformed CSV quoting"` error. Real cause: Python stdlib's `csv.field_size_limit()` default (128KB), caught by a generic `except csv.Error` and mislabeled.
- Média — BR decimal-comma (`"80,50"`) in a `;`-delimited file stays as `object` dtype, never normalized to float.

**Punch-list**: Alta — fix `_extract_docx_text` to also read `document.tables`; fix the misleading field-size-limit error message (distinguish from real malformed quoting, or raise `csv.field_size_limit()`). Média — BR decimal-comma normalization. Baixa — document explicitly that custom `query` on sqlite/mysql/postgres sources is trusted as LLM-plan-generated, not raw user input (this recommendation predates and is now superseded by Red Team's finding that this trust boundary is itself the critical vulnerability — see below).

#### LLM/Prompt Engineer

**✅ Verified via 2 real, credentialed end-to-end runs** (`gpt-4o-mini`, real cost ~$0.0006-0.005): Scenario 1 official case-study spec (Silver-only, empty question) completed real, quality_score 88.0. A second, original composite business question exercised Planner→Analyst→Science→Advisor→Reviewer fully — quoted real generated code, real narrative, real Advisor recommendations (checked the math, correct). All 5 PR #109 bugs reconfirmed fixed in current code, no regression.

**❌ Real findings from this execution, not speculation**:
- **Alta** — Planner doesn't strip the prescriptive part of a compound question ("...and recommend one action...") before decomposing — it classified that sub-question as `descriptive` and routed it to the **Analyst**, which produced a shallow, generic recommendation duplicating (and competing with) the Advisor's own, better-contextualized one. Reproduced directly.
- **Alta** — Science's narrative for a trend question ("is revenue trending up or down") hedged ("increased in some months, decreased in others") despite the underlying computed delta being clearly negative — a real non-committal answer to a question that explicitly asked for a direction. **The ADR-037 LLM-review layer itself returned `severity: ok`/"consistent" for this exact hedging narrative** — a real, reproduced blind spot: the reviewer checks factual/data consistency, not whether a directional question actually got a directional answer.
- Média — `advisor.py:192-199` duplicates markdown-fence-stripping logic manually instead of reusing the shared `agents/_llm_codegen.py::strip_code_fences` every other agent already imports — functionally fine today, real drift risk if the shared helper is ever fixed for an edge case.
- Média — Analyst mislabeled a product-level chart axis as "Categoria de Produto" when the dataset has no category column, only individual products — a real semantic slip (not a numeric hallucination).
- Baixa — Orchestrator's prompt has no few-shot JSON example despite having the most complex schema in the system (7 source types, 4 REST auth shapes); Planner has one, Orchestrator doesn't.

**Punch-list**: Alta — instruct the Planner to exclude the prescriptive/"recommend" clause from Gold/Science decomposition; extend the ADR-037 reviewer to check "did a directional question get a committed directional answer." Média — dedupe `advisor.py`'s fence-strip; add terminology-parity instruction to Analyst. Baixa — add a few-shot example to the Orchestrator prompt.

### Batch 3 — Security & Ops

#### Red Team

**✅ Held under real attack**: `validate_table_name()` rejected every SQLi payload tried (classic injection, homoglyphs, null bytes, comment injection) across both `allow_dots` modes (one cosmetic-only trailing-`\n` regex quirk, not exploitable, noted LOW). Tenant isolation code-verified for 3 endpoint families (`GET /runs/{id}`, `GET/PATCH /pipelines/{id}`, `POST /runs/{id}/approve|reject`) — always a `tenant_id`-scoped SQL `WHERE`, never app-layer-only. Secrets never leaked to git history or log lines (only 2 grep hits, both proven safe — log the secret's *name*, never its value). Auth (`verify_session_token`) rejected all 5 forged JWT attempts for real (unsigned/`alg:none`, expired, wrong-key-signed, RS256→HS256 key-confusion, wrong issuer) — the hardcoded `_ALGORITHMS=["RS256"]` allowlist alone stops the confusion attack before signature verification even runs. Sandbox introspection escape (`().__class__.__mro__[1].__subclasses__()`) reproduced for real (got a real shell via `subprocess.Popen`, read `/etc/passwd` for real) — but the one mitigation that actually matters, `os.environ.clear()` blocking secret exfiltration, was independently re-verified: canary secrets never appeared in the child even via `Popen(['env'])`/`os.environ` reached through object introspection.

**❌ CRITICAL, real, exploited**: `agents/pipeline/extractor.py:60,62` passes `source.get("query")` straight into `load_sqlite()`/`load_mysql()` with zero validation when present, and `pipeline_plan` (the Orchestrator LLM's JSON output, `orchestrator.py:93`) has no schema validation stripping unexpected keys. Built `{"type":"sqlite","table":"users","query":"DROP TABLE users; --"}`, ran it through the real `extractor_node()` against a throwaway seeded SQLite DB — **the table was actually dropped**, confirmed via a follow-up query failing with `no such table: users`. Any tenant able to submit a `spec`/business question could, via a sufficiently adversarial spec (prompt injection targeting the Orchestrator), potentially get this `query` field emitted and executed raw, including destructive DDL, against their own source DB. The `postgres` source path does not currently pass `query` through — only `sqlite`/`mysql` (and `mongodb`, which has its own separate `$where`/`$function` blocks) are affected this way.

**Punch-list**: CRÍTICO — add strict schema validation to `pipeline_plan` (allowlist per-source-type keys; reject/strip an unexpected `query` field on `sqlite`/`mysql`, or validate it the same rigorous way `mongodb`'s server-side-JS operators are already blocked) before `extractor_node` ever consumes it. Baixa — tighten `validate_table_name`'s regex to `re.fullmatch`/`\Z`-anchored for defense in depth against the trailing-newline quirk. No action needed — auth, tenant isolation, secrets handling, sandbox env-clearing, and the budget/rate-limit lock all held.

#### Blue Team

**✅ Verified**: Sentry `init_sentry()` confirmed actually running in production via real `railway logs` output ("Sentry initialized (component=api)"/"(component=worker)", timestamped). Read the real trigger logic: any `logging.ERROR`+ call anywhere becomes a Sentry event automatically, no new instrumentation needed anywhere. Most of the Agentic BI error trail is genuinely covered (Analyst/Science/Advisor failures persist into the run's JSON manifest via the existing `"error"` field, not just an ephemeral log line).

**⚠️/❌ Real gaps**:
- Could not verify an actual Sentry event reaching the dashboard without extracting the real `SENTRY_DSN` (correctly declined to do so — the Railway CLI call was blocked by the permission classifier and the agent didn't try to route around it). Honestly reported as unverified, matching ADR-033's own already-flagged gap.
- **Real, traced (not executed, but deterministic pure-function logic)**: `audit/logger.py::_sanitize` only matches keys literally containing `key`/`token`/`secret`/`password`/`credential` — `authorization`/`Authorization`/`Bearer` headers are NOT caught, and the function never recurses into nested dicts at all, so any secret one level deep (`{"headers": {"authorization": "..."}}`) passes through untouched into the persisted audit log. `logging_config.py`'s `JsonFormatter` explicitly does zero redaction of its own (documented in its own docstring) — `_sanitize` is the *only* barrier, and it has a real, describable hole.
- Real, pointed gap: Planner decomposition failures are the one Agentic BI failure mode with **no persisted record at all** — only an ephemeral `logger.warning()`, unlike Analyst/Science/Advisor which all persist their error into the run manifest.
- No incident-response runbook exists anywhere (`docs/`, `README.md`, vault) — confirmed by the project's own SOC2 self-assessment already flagging this as an open P2 gap, unaddressed since it was written.
- Rollback: Railway deployment history is real and inspectable, but there's no dedicated "rollback to deployment X" tool/documented procedure tested by the owner — plausible the platform supports it via dashboard, but unverified/untested.

**Punch-list**: Alta — fix `_sanitize` to recurse into nested dicts and broaden the keyword list (`authorization`, `bearer`, etc.); write a minimal 1-page incident runbook (already a self-documented, unaddressed P2 gap); force one real controlled exception through to confirm actual Sentry event capture. Média — persist Planner decomposition failures somewhere queryable; document/test the real Railway rollback procedure. Baixa — add an explicit code comment near every `logger.warning`/`logger.error` that could carry user payload, flagging that `JsonFormatter` does no redaction of its own.

#### SRE/DevOps

**✅ Verified live**: all 3 live surfaces up (API `/health`/`/docs` 200, frontend 200). All 3 Railway services (`ai-etl`, `tranquil-appreciation`, `celery-beat`) SUCCESS, no restart-loop pattern in recent deploy history. CI: 20/20 green on `main` across the last 10 merges (`gh run list`). Migration state: production `alembic_version` = `0020`, matches the repo's newest migration file exactly — zero drift. Celery beat confirmed firing for real in production logs (`Scheduler: Sending due task check-scheduled-pipelines` every ~60s, continuous 30-minute window, zero gaps/errors). Vercel alias currently in sync (points at the latest deployment) — the documented risk is structural, not currently manifesting.

**❌ Real gaps**:
- `ai-etl`'s Railway service config has **no `healthcheckPath` set at all** (confirmed via `get-service-config`) — Railway's "SUCCESS" only means "container started," not "the app answers `/health` with 200." A deploy that starts but serves errors on every real route would still swap traffic over with zero automated gate.
- `tests/integration/` reproduced broken today against a real, freshly-migrated local Postgres — the exact 2 bugs `CURRENT_STATE.md` already documents (stale `tenant_id NOT NULL` test fixtures; an Alembic-test `metadata.create_all()` collision on a persistent, not ephemeral, DB). Confirms the doc is accurate, not stale.
- Secondary finding: `celery-beat`'s own container logs show `"Sentry not configured (SENTRY_DSN unset)"` — confirmed via `railway variables` that `celery-beat` genuinely has no `SENTRY_DSN` (only `ai-etl`/`tranquil-appreciation` do) — a beat-process crash before a scheduled task even fires would be invisible to Sentry.
- Vercel alias mechanism is unchanged (still a manually-assigned, not auto-following, domain) — currently fine, will need a manual `vercel alias set` again after some future deploy with no automated alert if forgotten.

**Punch-list**: Alta — add a Railway healthcheck against `/health` so a broken deploy fails to promote; `tests/integration/`'s 2 real bugs (contributes zero real coverage today, self-skips in CI). Média — add `SENTRY_DSN` to `celery-beat`; document/alert on the Vercel manual-alias risk. Baixa — pre-existing `SECURITY.md`/ADR-003 staleness (already known).

### Batch 4 — QA & UX

#### QA / Test Automation

**✅ Verified via own real run**: `pytest tests/unit --cov` → 930 passed, 94% real coverage (matches `CURRENT_STATE.md` exactly — independently confirmed, not trusted). Mock-density analysis across ~10 representative files: mocking is used correctly at real I/O boundaries (LLM, DB, HTTP) while preserving real-behavior assertions on the actual business logic in the vast majority of the suite — estimated <10% of the suite is pure-mechanics assertion. `tests/e2e/` (5 suites) actually run to completion locally with real Postgres+Redis (5/5 passed) and are genuinely non-shallow (verified real dedup/filter/row-count/audit-trail-round-trip behavior in `test_scenario1_csv.py`) — corrects the standing assumption that e2e "can't run locally." Reproduced a `SystemExit`-in-sandboxed-code edge case for real: correctly blocked (`SAFE_BUILTINS` doesn't expose it), and the "process died without a result" branch was forced for real and correctly returns an explicit error message rather than hanging.

**❌ Real findings**:
- `tests/integration/test_alembic_migration.py`/`test_audit_persistence.py` genuinely fail against today's real schema (independently reproduced — matches SRE's finding) — root cause read directly in code: an incomplete cleanup fixture and stale-since-ADR-006 test assertions, already flagged (not fixed) in `.github/workflows/ci.yml`'s own comments.
- `sources/postgres_source.py::load_postgres` has zero test coverage, not even mocked (38% file coverage, the 8-line real function never exercised at all).
- `api/routers/admin.py` (cross-tenant admin endpoints) has zero direct endpoint test — only the underlying persistence layer (`audit/admin_log.py`) is tested, never the actual FastAPI wiring/auth gate.

**Punch-list**: Alta — fix the 2 known-broken integration tests; add direct tests for the admin router (auth gate + response shape). Média — add a real test for `load_postgres`; consider giving `tests/integration/`/`tests/e2e/` a CI job with real Postgres by default rather than letting integration rot indefinitely. Baixa — minor coverage gaps in `mysql_source.py`/`postgres_dest.py`/`api/serialization.py`, and the untestable-by-design sandbox SIGKILL escalation branches.

#### Non-technical end user (live browser, in-character)

**✅ Worked well, verified live**: landing page's "why not just paste into ChatGPT" section read as honest, not salesy, to this persona. Empty-form validation error was clear, in Portuguese. `/comecar`'s "Usar exemplo (vendas)" pre-fill removed the "I have no data to test with" barrier entirely. The live agent-progress stepper visually communicated "it's running," even without understanding the stage names. The "Como funciona" (i) panel successfully translated the Bronze/Silver/Gold/Science/Advisor jargon into plain language — but only once inside the app, never on the public landing page. The Advisor tab produced genuinely plain-language, prioritized business recommendations. `/resumo`'s empty state ("this pipeline hasn't run yet — come back after its first scheduled run") was clear and well-written.

**❌ Real gaps, found live, not read from code**:
- **The most consequential finding**: the Gold tab's chart showed "Norte" as the highest-revenue region; the narrative text above it said "Sudeste" had the highest revenue; a yellow sanity-check warning box appeared, **in English**, correctly stating the narrative was wrong ("*The narrative incorrectly states the revenue for 'Sudeste'... the preview shows 'Norte' with the highest revenue*"); and the Advisor tab's final business recommendation **used the wrong "Sudeste" number anyway, with no mention of the warning at all**. As the executive-persona user this product explicitly targets, this persona would have made a real business decision on a number the product itself already knew was wrong — and never have seen the warning, since it was in English and buried.
- An upload-parsing error rendered as raw English (`"Error: Could not parse uploaded file."`) inside an otherwise 100%-Portuguese app.
- The `/pipelines` scheduling form's source-type field literally displays "(apenas fontes 'vivas' podem ser agendadas — **ADR-016**)" — an internal architecture-decision-record number leaked verbatim into production UI text.
- `/historico` entries are titled by raw UUID and file path (e.g. *"Read the file at runs/uploads/926ed592042f.csv..."*) — meaningless to this persona.
- A `customer_id` value rendered as a float (`281.57894736842104`) in the Silver data table, with no explanation — reads as an obvious bug to a lay user even without knowing why.
- A stray developer test artifact (a saved pipeline literally named `"t"`) was visible in the account being tested.
- Could not test the real anonymous sign-up flow (the browser already carried an authenticated session) — flagged honestly as unverified, not assumed fine.

**Punch-list**: Alta — fix the Advisor-ignores-its-own-warning contradiction (same root cause the LLM/Prompt persona found independently by code); translate every user-facing error/status string consistently into Portuguese; strip internal ADR references from production UI copy. Média — give run-history entries human-readable titles instead of raw UUIDs/paths; surface the Bronze/Silver/Gold/Science/Advisor glossary somewhere more discoverable than the hidden "i" panel; investigate the fractional `customer_id` display bug. Baixa — currency/number formatting consistency; clean stray test data from the demo-visible account; consider a custom Clerk domain to reduce first-time sign-up unfamiliarity.

#### Accessibility

**✅ Verified live, via real browser automation**: full keyboard-only traversal of the landing page — logical tab order, a visible focus ring on every interactive element, Enter/Escape both worked correctly (activating the theme toggle, opening/closing the user menu and the `MobileNav` drawer). 8 real text/background color pairs measured via actual computed RGB in both themes — all pass WCAG AA (lowest was 4.74:1 for muted light-mode captions, still passing but with little margin). `MobileNav` (Sprint 38) is genuinely present and functional at a ~600px viewport: opens as a real `role="dialog"`, correct tab order inside it, Escape closes it for real, link navigation works. `/historico`'s wide table is correctly wrapped in its own `overflow-x: auto` container — no document-level horizontal overflow. Exactly one `<h1>` per page confirmed on 2 pages checked; the one real `<img>` (Clerk avatar) has real `alt` text; all 3 fields on `executar-form.tsx` have real `<label for="...">` associations, not just placeholder text.

**❌ Real gaps**:
- The locale-toggle button's visible text **and** `aria-label` both literally render the raw, untranslated i18n key `"localeToggle.switchTo"` — a screen-reader user would hear this nonsense string read aloud.
- `agent-progress.tsx`'s live stepper has **zero** `aria-live`/`role="status"` — confirmed via real DOM inspection during an actual live run (`stepperIsLive: false`). A screen-reader user gets no notification of stage changes or of the run's final success/failure state; the page's only `aria-live` region is an unrelated, empty container.
- Could not test exactly 375px (iPhone SE) due to a tool/environment floor of ~591-600px this session — the ~600px result was clean, but the exact narrow-phone case remains genuinely unverified, not assumed fine.
- Minor: 5 of 9 decorative SVGs on `/app` lack `aria-hidden="true"` (4 of 9 have it) — inconsistent, low severity.

**Punch-list**: Alta — add a real `aria-live`/`role="status"` announcement to the agent-progress stepper (currently 100% invisible to screen readers); fix the untranslated `localeToggle.switchTo` key. Média — re-verify at an exact 375px viewport with a different tool/environment; make decorative-SVG `aria-hidden` usage consistent. Baixa — keep an eye on the muted-caption contrast ratio in light mode if that token is ever darkened further.
