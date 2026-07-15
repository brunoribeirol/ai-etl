# Phase 0/1 — Baseline verification + pipeline orchestration service extraction

**Status:** Completed
**Branch:** `refactor/extract-pipeline-service`
**Scope:** Stabilize the current behavior and extract the Silver → Planner →
Gold/Science → Advisor orchestration out of `app.py` into a Streamlit-independent
service layer. No new infrastructure, no new product features.

---

## 1. Estado anterior

All orchestration logic lived inside `app.py`, mixed directly with Streamlit calls:

- `_run_silver_pipeline`, `_run_gold_analysis`, `_run_science_analysis`,
  `_run_gold_with_repair`, `_run_science_with_repair`, `_run_analysis_tasks`,
  `_run_advisor_analysis`, `_sum_run_tokens` were private functions in `app.py` that
  called `st.status(...)` directly to report progress, and the full sequence (Silver
  → Planner → Gold/Science → Advisor → persistence) was inlined in the
  `st.button("▶️ Analisar dados")` click handler (`app.py`, previously lines 661–719).
- This made the orchestration:
  - **Untestable without Streamlit** — the only way to exercise the full sequence was
    `streamlit.testing.v1.AppTest`, which boots the entire script.
  - **Unusable from any other caller** — a future worker, batch script, or the CLI
    could not reuse this sequencing without also depending on Streamlit.
  - **Duplicated** — `AGENT_STEPS` (emoji/label/description per LangGraph node) was
    defined once for orchestration and read again for rendering the "Pipeline" tab,
    an obvious drift risk.

## 2. Problemas resolvidos

- Orchestration logic is now presentation-agnostic and independently testable
  (`tests/unit/test_pipeline_service.py`, 16 tests, all mocking the LLM-backed agents
  — no real LLM calls, no Streamlit).
- `app.py` no longer contains the sequencing logic; it calls
  `ai_etl.services.pipeline_service.run_full_analysis(...)` and adapts a single
  `ProgressCallback` to `st.status` boxes.
- `AGENT_STEPS` now has one source of truth in `pipeline_service.py`, imported by
  `app.py` for the "Pipeline" tab instead of being redefined.
- Confirmed (not fixed, out of scope) two pre-existing issues found during baseline
  verification, documented in section 11 below.

## 3. Arquivos alterados

| File | Change |
|---|---|
| `src/ai_etl/services/__init__.py` | **New.** Package docstring stating the "no streamlit" contract. |
| `src/ai_etl/services/pipeline_service.py` | **New.** All orchestration functions extracted from `app.py`, taking a `ProgressCallback` instead of calling `st.status` directly. |
| `src/ai_etl/core/analysis_types.py` | Added `AnalysisRunResult` TypedDict — the return contract of `run_full_analysis`. |
| `app.py` | Removed `_run_silver_pipeline`, `_run_gold_analysis`, `_run_science_analysis`, `_run_gold_with_repair`, `_run_science_with_repair`, `_run_analysis_tasks`, `_run_advisor_analysis`, `_sum_run_tokens`, and the local `AGENT_STEPS` dict. Added `_make_progress_adapter()` (Streamlit `st.status` adapter) and rewired the button handler to call `run_full_analysis`. |
| `tests/unit/test_app.py` | Removed `test_sum_run_tokens_*` (moved to `test_pipeline_service.py`, since the function moved). Added 3 tests for `_make_progress_adapter`. |
| `tests/unit/test_pipeline_service.py` | **New.** 16 tests covering the extracted service (see section 7). |
| `docs/PHASE_0_SERVICE_EXTRACTION.md` | **New.** This document. |

No other file was touched. `src/ai_etl/agents/*.py` (prompts, analytical rules,
response formats), `src/ai_etl/core/sandbox.py`, `src/ai_etl/audit/db.py` (SQLite
schema), `src/ai_etl/__main__.py` (CLI), `Makefile`, `pyproject.toml`,
`docker-compose.yml` — all unchanged.

## 4. Arquitetura antes e depois

**Antes:**

```
app.py
  └─ st.button("Analisar dados") click handler
       ├─ _run_silver_pipeline(spec)              # st.status inline
       ├─ _run_analysis_tasks(df, question)        # st.status inline
       │    ├─ plan_analysis_tasks(...)
       │    ├─ _run_gold_with_repair(...) × N       # st.status inline
       │    └─ _run_science_with_repair(...) × N    # st.status inline
       ├─ _run_advisor_analysis(...)                # st.status inline
       ├─ save_analysis(...)
       └─ st.session_state["pipeline_result"] = {...}
```

**Depois:**

```
app.py                                    ai_etl/services/pipeline_service.py
  └─ st.button(...) click handler           run_full_analysis(spec, question, run_dir, cb)
       ├─ _make_progress_adapter()  ──cb──▶   ├─ run_silver_pipeline(...)      [save_run]
       │                                       ├─ run_analysis_tasks(...)
       │                                       │    ├─ plan_analysis_tasks(...)
       │                                       │    ├─ run_gold_with_repair(...) × N
       │                                       │    └─ run_science_with_repair(...) × N
       │                                       ├─ run_advisor_analysis(...)
       │                                       └─ save_analysis(...)          [only if Silver produced rows]
       └─ st.session_state["pipeline_result"] = {...}   ◀── AnalysisRunResult
```

`pipeline_service.py` imports only `ai_etl.agents.*`, `ai_etl.audit.db`,
`ai_etl.core.*`, `pandas` — no `streamlit`, verified by a dedicated test
(`test_services_package_does_not_import_streamlit`).

## 5. Funções movidas

| Old name (`app.py`, private) | New name (`pipeline_service.py`, public) | Behavior change |
|---|---|---|
| `_run_silver_pipeline(spec)` | `run_silver_pipeline(spec, run_dir, progress_callback)` | None — `RUNS_DIR` is now an explicit `run_dir` parameter instead of a module global. |
| `_run_gold_analysis(df, q)` | `run_gold_analysis(df, q, progress_callback, stage)` | None — added `stage` param so callers can route multiple concurrent sub-tasks to distinct progress "channels". |
| `_run_science_analysis(df, q)` | `run_science_analysis(df, q, progress_callback, stage)` | None |
| `_run_gold_with_repair(df, q)` | `run_gold_with_repair(df, q, progress_callback, stage)` | None |
| `_run_science_with_repair(df, q)` | `run_science_with_repair(df, q, progress_callback, stage)` | None |
| `_run_analysis_tasks(df, q)` | `run_analysis_tasks(df, q, progress_callback)` | None — now assigns explicit `gold:{i}` / `science:{i}` stage keys per sub-task instead of Streamlit implicitly nesting status boxes. |
| `_run_advisor_analysis(df, q, gold, science)` | `run_advisor_analysis(df, q, gold, science, progress_callback)` | None |
| `_sum_run_tokens(...)` | `sum_run_tokens(...)` | None — pure function, moved as-is. |
| *(inlined in the button handler)* | `run_full_analysis(spec, question, run_dir, progress_callback)` | **New top-level function**, not a rename — composes the sequence above, including the Silver-failed/empty short-circuit that previously lived directly in the button handler. |

## 6. Contratos preservados

- `GoldResult`, `ScienceResult`, `AdvisorResult`, `AnalysisTask`, `TokenUsage`
  (`core/analysis_types.py`) — untouched, reused as-is by the service functions.
- **New:** `AnalysisRunResult` TypedDict, the return contract of `run_full_analysis`.
  Its `advisor` field is typed `AdvisorResult | dict[str, Any]` — this is not a
  looseness introduced by the refactor, it documents an existing behavior: when
  Silver fails or returns no rows, the original code left `advisor_result: dict = {}`
  (never a real `AdvisorResult`), and `_render_results` in `app.py` relies on that
  empty dict being falsy (`if not advisor: st.info("Recomendações não
  disponíveis...")`). This sentinel is preserved exactly — a test
  (`test_run_full_analysis_short_circuits_when_silver_produces_no_data`) pins it down.
- `st.session_state["pipeline_result"]` keeps the exact same shape as before
  (`state`, `bronze`, `gold`, `science`, `advisor`, `question`, `tokens`) — `app.py`
  composes it from `AnalysisRunResult` plus `bronze` (which stays UI-side, since it's
  read directly from the Streamlit-uploaded file before the service is called).

## 7. Testes adicionados

`tests/unit/test_pipeline_service.py` — 16 tests, all mocking the agent functions
(`run_analyst`, `run_science`, `run_advisor`, `plan_analysis_tasks`) and persistence
(`save_run`, `save_analysis`) at the module boundary; no LLM calls, no filesystem
writes, no Streamlit:

1. `test_run_silver_pipeline_emits_progress_and_persists` / `..._reports_failure` —
   Silver sequencing, `save_run` called exactly once with the right `log_dir`,
   progress messages match the original text.
2. `test_run_gold_analysis_emits_progress_events` /
   `test_run_science_analysis_emits_progress_events` — single-attempt path.
3. `test_run_gold_with_repair_returns_first_result_when_it_succeeds`,
   `..._retries_with_simplified_question_on_failure`,
   `..._surfaces_original_error_when_both_attempts_fail`,
   `test_run_science_with_repair_retries_on_failure` — the auto-repair retry
   contract (fallback question rewritten, `repaired: True` only on recovery,
   original error surfaced if both attempts fail).
4. `test_run_analysis_tasks_routes_by_type_and_stages_each_subtask` — Planner output
   routes `descriptive` → Gold and `diagnostic_or_predictive` → Science, one
   `gold:{i}` / `science:{i}` stage per sub-task.
5. `test_run_advisor_analysis_emits_progress_events`.
6. `test_sum_run_tokens_aggregates_across_all_calls` /
   `..._handles_missing_tokens_key` — moved from `test_app.py` (function moved).
7. `test_run_full_analysis_calls_stages_in_order_and_persists_analysis` — full
   sequencing (silver → planner → gold → advisor → save_analysis) and that
   `save_analysis` receives the right `run_id`/`log_dir`.
8. `test_run_full_analysis_short_circuits_when_silver_produces_no_data` — Planner/
   Gold/Science/Advisor/`save_analysis` are **not** called when Silver fails or
   returns an empty/`None` DataFrame; `advisor` comes back as `{}`.
9. `test_run_full_analysis_forwards_progress_callback_to_every_stage`.
10. `test_services_package_does_not_import_streamlit` — fails the build if any file
    under `src/ai_etl/services/` imports `streamlit`.

`tests/unit/test_app.py` — added 3 tests for `_make_progress_adapter` (opens one
`st.status` box per stage, finalizes on a "done" glyph, treats ⚠️ as an error state
— matching the original code's own convention). Removed the two `_sum_run_tokens`
tests (function moved, tests moved with it). The existing `test_app_boots_without_exception`
and `test_app_renders_welcome_screen_with_no_upload` (AppTest, boots the real script)
continue to pass unmodified and are the primary regression guard for `app.py`'s
control flow.

## 8. Comandos executados

```bash
git status                                              # baseline check
grep -n "^[a-zA-Z_-]*:" Makefile                        # confirm available targets
grep -rn "AI_ETL_LOG_DIR|AI_ETL_MAX_RETRIES|AI_ETL_SANDBOX_TIMEOUT" .   # env var usage
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest tests/unit/ tests/integration/ -q --cov=src/ai_etl --cov-report=term-missing --cov-fail-under=80
uv run bandit -c pyproject.toml -r src/ai_etl/
uv run pip-audit
uv run python -m ai_etl                                 # CLI sanity check
uv run streamlit run app.py --server.headless true --server.port 8532   # live boot check
git diff --check
git status
```

## 9. Resultados das validações

| Validation | Baseline (before) | After extraction |
|---|---|---|
| `ruff check` | ✅ pass | ✅ pass |
| `ruff format --check` | ✅ pass (51 files) | ✅ pass (54 files) |
| `mypy src/` (strict) | ✅ pass (28 files) | ✅ pass (30 files) |
| `pytest` (unit + integration) | 168 passed | **185 passed** |
| Coverage | 93.82% | **94.61%** (`pipeline_service.py` itself: 99%) |
| `bandit` | 0 issues | 0 issues |
| `pip-audit` | no known vulnerabilities | no known vulnerabilities |
| `git diff --check` | — | clean, no whitespace errors |
| Real `streamlit run app.py` boot | — | HTTP 200, no exception in logs (verified, then stopped) |
| CLI (`python -m ai_etl`) | pre-existing local `ModuleNotFoundError` (macOS `.pth` hidden-flag issue, see below) | same pre-existing issue, confirmed unrelated to this change (reproduced identically on `main`) |

## 10. Limitações que permanecem

- The Streamlit progress UI is **not pixel-identical** to before: originally, each
  phase used a `with st.status(...) as box:` context manager with nested boxes for
  retries (e.g. Gold's repair attempt opened its own nested status inside the outer
  Gold status). The adapter (`_make_progress_adapter` in `app.py`) instead opens one
  flat `st.status` box per `stage` key (`"gold:0"`, `"gold:0:repair"`, etc.) and
  finalizes it based on a message's leading glyph (✅/❌/⚠️) — same convention the
  original code already used for its final `.update()` call on each box. This does
  not affect the final rendered result: after the pipeline finishes, `st.rerun()`
  discards every status box and only `_render_results(...)` remains, which never
  reads from the status boxes' content.
- Per-node `agent_timings` in the Silver run are still ~0 by construction (timed from
  immediately-before to immediately-after a state update, not across the node's
  actual execution) — a pre-existing quirk, kept as-is per the "preserve behavior,
  don't fix bugs" scope of this phase.

## 11. Riscos que não foram tratados nesta fase

Everything the audit flagged as out of scope remains out of scope:

- **Sandbox real** (Docker/E2B) — `core/sandbox.py` and the in-process `exec()` in
  `analyst.py`/`science.py` are untouched.
- **Autenticação / Autorização / Multi-tenancy** — no login, no `user_id`, `runs/`
  still shared globally.
- **Postgres de aplicação** — `runs/runs.db` (SQLite) untouched; Postgres remains
  only a source/destination for the user's own pipelines.
- **Object storage** — `runs/uploads/` stays on local filesystem, no TTL/cleanup.
- **Redis / RQ / workers / execução assíncrona** — the "Analisar dados" button still
  runs everything synchronously in the Streamlit process; `run_full_analysis` is
  *callable* from a future worker (it has no Streamlit dependency), but nothing wires
  it to one yet.
- **Projetos, follow-up, múltiplos uploads** — no new product functionality was
  added; `run_full_analysis` still takes exactly one `spec` and one
  `business_question`.

Two items confirmed during this phase's baseline inspection (documented, not fixed):

- **JSON upload is only previewed, not actually pipelined**: `st.file_uploader`
  accepts `.json`, and `_read_uploaded_file` (`app.py`) parses it fine for the Bronze
  preview via `pd.read_json`, but `ai_etl.sources.csv_source.load_csv` (used by the
  Extractor for every `csv`-typed source, which is what `_auto_generate_spec` always
  produces) only branches on `.xlsx`/`.xls` vs. everything else via `pd.read_csv` —
  a `.json` path would be read as CSV by the Extractor and most likely fail. Silver
  pipeline JSON support does not exist end-to-end today.
- **`AI_ETL_LOG_DIR`, `AI_ETL_MAX_RETRIES`, `AI_ETL_SANDBOX_TIMEOUT`** — confirmed via
  `grep -rn` across the whole repository: these three variables appear only in
  `.env.example` and nowhere else. They are documented but not wired to any code path
  (retries and the run log directory are hardcoded/passed as explicit function
  arguments; the sandbox's `timeout_seconds` parameter exists but is never enforced
  and is not read from an env var).
- **`uv run python -m ai_etl` / `uv run python -c "import ai_etl"` fails locally**
  with `ModuleNotFoundError: No module named 'ai_etl'` immediately after `uv sync`,
  even after running `make unhide-pth` — this reproduces identically on `main`
  (verified by checking out `main`, running `uv sync --all-extras`, and hitting the
  same error). It's the documented macOS/uv `.pth` hidden-flag issue (see `Makefile`
  comment above `unhide-pth`); `pytest` is unaffected because
  `pyproject.toml`'s `[tool.pytest.ini_options] pythonpath = ["src", "."]` injects the
  path directly, bypassing the broken editable install. Re-running
  `chflags nohidden .venv/lib/python3.*/site-packages/*.pth` manually after `uv sync`
  fixes it for the current shell session. Not fixed in this phase — pre-existing,
  unrelated to the extraction.

## 12. Próximos passos recomendados

Per the audit's 8-phase plan (see the technical audit report from the previous
session), with this phase done:

1. **Phase 2 — Persistence and models**: replace `runs/runs.db` (SQLite, no
   `user_id`) with a real Postgres schema for the application (not to be confused
   with the Postgres used as a pipeline source/destination). `run_full_analysis`'s
   `run_dir: str` parameter already isolates *where* persistence happens, making the
   swap from `save_run`/`save_analysis` (file-based) to a DB-backed implementation a
   contained change.
2. **Phase 3 — Auth and isolation** depends on Phase 2's schema existing.
3. **Phase 4 — Queue and workers**: `run_full_analysis` has no Streamlit dependency,
   so it is already callable from a worker process as-is; this phase would add the
   queue (Redis/RQ) and a worker entry point that calls it, replacing the synchronous
   `st.button` click handler in `app.py` with an enqueue + poll/websocket pattern.
4. Investigate the JSON-upload gap (section 11) — either wire a JSON loader into
   `sources/`, or stop advertising `.json` as a supported upload format in the UI,
   whichever the product decision favors.
