# ADR-031: Multi-provider LLM — from infrastructure to product

**Status:** Accepted
**Date:** 2026-08-22
**Sprint:** 30 (Fase 2 product roadmap, "Nível Big Tech")

## Context

Sprint 23 (ADR-014) made `core/llm.py` support four providers — OpenAI, Anthropic, Google,
Ollama — selected via `AI_ETL_LLM_PROVIDER`, a single env var for the whole deployment. Two
gaps that ADR explicitly flagged as deferred, not solved, turned into real product/billing
risk by the time of the 2026-08-22 pre-launch audit:

1. **`core/pricing.py` only priced 2 OpenAI models.** Running a pipeline against
   Anthropic/Google made `compute_cost_usd()` return `None` ("cost unknown") for every call,
   which `audit/db.py`'s cost-per-run persistence and the Sprint 29 tenant budget cap both
   read as effectively `$0.00` — a tenant on Claude or Gemini could burn real spend while the
   product's own budget cap silently saw nothing to compare against.
2. **Provider/model selection was deployment-global, not a client-facing choice.** `GET
   /config` was (and stays) read-only; there was no way for a tenant to pick a provider/model
   for a specific `saved_pipeline`, and no allowlist to validate that choice against — the
   same kind of validation `source_type` already gets (`SCHEDULABLE_SOURCE_TYPES`, ADR-016
   Decision 3).
3. **Real connectivity was never verified.** ADR-014's own Consequences section says plainly:
   "provider selection is verified with mocked env vars... never by making a live API call."
   That gap survived to this sprint.

**Definition of done (roadmap):** a tenant configures Claude or Gemini for a pipeline, the
execution uses the provider chosen, and cost appears correctly against the budget cap — never
`$0`/`None` by omission.

## Decision

### 1. Pricing: extend the existing table, add explicit $0 Ollama entries

`core/pricing.py`'s `MODEL_PRICING_USD_PER_MILLION_TOKENS` gets one entry per model in the new
`core/llm.ALLOWED_MODELS_BY_PROVIDER` allowlist (below): 3 Anthropic tiers, 2 Google tiers, and
every allowed Ollama model priced at an explicit `{"input": 0.0, "output": 0.0}` rather than
left absent. `compute_cost_usd()`'s own contract (`None` = "unpriced/unknown",
distinct from a real `0.0`) is unchanged — Ollama is the one case where `0.0` genuinely is the
correct, non-misleading answer, so it gets a real entry instead of relying on the `None`
fallback. A new test (`test_every_llm_allowed_model_has_a_pricing_entry`) asserts every
allowlisted model has a pricing row, so the two tables can't drift apart silently again.

### 2. A provider/model allowlist, reusing the `source_type` validation pattern

`core/llm.py` gains `ALLOWED_MODELS_BY_PROVIDER: dict[str, frozenset[str]]` and
`validate_provider_and_model(provider, model) -> None` (raises
`UnsupportedProviderOrModelError`, a `ValueError` subclass distinct from the `RuntimeError`
`get_llm()` raises for deployment-level misconfiguration). This mirrors
`core/scheduling.SCHEDULABLE_SOURCE_TYPES` + `_validate_source_type` (ADR-016 Decision 3)
exactly: a fixed, reviewed allowlist, validated once at the API boundary before anything is
persisted. `get_llm()` itself stays permissive (unvalidated `AI_ETL_LLM_MODEL`, unchanged from
ADR-014) — the allowlist only gates the new client-facing selection endpoint, since a
deployment operator's own env var is not untrusted input the way a tenant's API request is.

### 3. Per-`saved_pipeline` override: two new columns, two new endpoints, additive-only `audit/db.py`

`saved_pipelines` gets two new nullable columns, `llm_provider`/`llm_model` (migration 0016,
`models.py`) — both `NULL` (every existing row, and every new row unless explicitly set) means
"no override, use this deployment's global `AI_ETL_LLM_PROVIDER`/`AI_ETL_LLM_MODEL`". Always
written or cleared together — a lone provider or a lone model can't validate against the
allowlist, so the API layer (`SetPipelineLlmConfigRequest`'s `_both_or_neither` validator)
rejects a partial pair with 422 before it ever reaches persistence.

`audit/db.py` gets two **new, standalone** functions appended at the end of the file —
`get_saved_pipeline_llm_config` / `set_saved_pipeline_llm_config` — rather than new parameters
on `create_saved_pipeline`/`update_saved_pipeline`/`get_saved_pipeline` or a rewritten
`_saved_pipeline_row_to_dict`. This is a hard constraint for this sprint, not a style
preference: Sprint 30 runs in a worktree parallel to Sprints 31 and 32, both of which may also
touch `audit/db.py`, and the batch's isolation rule for this file is "append-only, never edit
an existing function body" to keep all 3 PRs' diffs merge-safe. `api/routers/pipelines.py`
composes the two new functions onto the existing saved-pipeline dict in `_with_health`, the
same way that function already composes `get_pipeline_health` onto it — no new coupling, one
more field merged on read.

New endpoints, both under `/pipelines` (not a new top-level resource, since their only
consumer is the per-pipeline LLM config surface):

- `GET /pipelines/{id}/llm-config` — current override (or `null`/`null`).
- `PUT /pipelines/{id}/llm-config` — set or clear it (`editor` role, same as every other
  pipeline-mutating endpoint); validates a non-null pair against
  `ALLOWED_MODELS_BY_PROVIDER` before persisting, 400 on an out-of-allowlist choice.
- `GET /pipelines/llm/allowed-models` — the allowlist itself, for a future picker UI to render.

### 4. Real connectivity testing: `core.llm.test_provider_connectivity` + `POST /llm/test-connectivity`

`core/llm.py` gains `test_provider_connectivity(provider, model) -> dict` — builds a real
client via the existing `_BUILDERS` dispatch and makes one real, minimal `.invoke()` call,
catching every exception (missing credential, unreachable provider, invalid model id, network
error) and reporting `{"ok": bool, "provider", "model", "latency_ms", "error"}` rather than
raising. `POST /llm/test-connectivity` (new `api/routers/llm.py`) wraps it: 400 only for a
provider/model outside the allowlist (a client input error), 200 with `ok: false` for every
actual connectivity failure — a failed probe is exactly the useful, expected answer this
endpoint exists to give, not a 5xx.

**Verified in this environment:** `env | grep -iE "OPENAI|ANTHROPIC|GOOGLE"` and `.env`
inspection (keys only, no values) confirmed **no provider credentials are configured in this
sandbox** — no `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/`GOOGLE_API_KEY`, and no local Ollama
server reachable either. `test_provider_connectivity`'s control flow is exercised by real
unit tests (`tests/unit/test_llm.py::TestProviderConnectivity`) that fake only the
provider SDK's `.invoke()` call (via `monkeypatch.setattr(ChatOpenAI, "invoke", ...)`), not the
function's own logic — the success path, the "missing credential surfaces as `ok: false` with
the exact `_require_env` message" path, and the "SDK raises mid-call" path are all covered for
real. A genuinely live call against a real provider (the roadmap's "at least 1 non-OpenAI
provider" bar) is **not exercised** and is flagged here explicitly — same honesty pattern
ADR-014 already used for its own credential gap — pending whoever configures real credentials
in this sandbox or CI.

### 5. Explicitly out of scope

- **Frontend model/provider picker.** The roadmap's own scope line calls this out by name:
  "Frontend: seletor de modelo/provedor na tela de configuração de pipeline" is listed as an
  entregável, but this sprint was run backend-first, deliberately — same pattern already used
  in Sprints 20/27 (ship the API contract, defer the UI as an explicit, tracked follow-up
  rather than rushing both halves in one PR). `GET /pipelines/{id}/llm-config`,
  `PUT /pipelines/{id}/llm-config`, and `GET /pipelines/llm/allowed-models` are the full
  contract a picker needs; no frontend component exists yet.
- **Automatic cross-provider fallback** — still out of scope, ADR-014 §4 already covers this.

> **Update (gap-closing fix, post-Sprint 30):** "Actually wiring the chosen provider/model
> into pipeline execution" — originally flagged above as the one place this sprint's own
> Definition of Done wasn't met — is now closed. See §6 below for what changed, how, and the
> one residual gap that fix deliberately left open (cost tracking).

### 6. Gap-closing fix: the override now actually reaches execution

Sprint 31/32 having landed and `audit/db.py`'s append-only constraint no longer applying to
this file, the 7 real `get_llm()` call sites (`agents/pipeline/orchestrator.py`,
`agents/pipeline/transformer.py`, `agents/analysis/planner.py`, `agents/analysis/analyst.py`,
`agents/analysis/science.py`, `agents/analysis/advisor.py`, `sources/document_source.py` — one
more than §5's original count of 6, since that count missed the document connector) now
respect a `saved_pipeline`'s `llm_provider`/`llm_model` override, following the exact shape §5
sketched:

- **`get_llm()` gains optional `provider`/`model` parameters**, both `None` by default —
  every existing call site that doesn't pass them keeps reading `AI_ETL_LLM_PROVIDER`/
  `AI_ETL_LLM_MODEL` unchanged. Not re-validated against `ALLOWED_MODELS_BY_PROVIDER` here —
  by the time an override reaches `get_llm()` it was already validated once, at the `PUT
  /pipelines/{id}/llm-config` API boundary that persisted it, matching Decision 2's "validate
  once, at the API layer" posture.
- **`PipelineState` gains `llm_provider_override`/`llm_model_override`** (both
  `Optional[str]`), resolved once in `services/pipeline_service.py::run_silver_pipeline` via
  the new `get_saved_pipeline_llm_config` lookup (same gate as `custom_quality_rules`/
  `approval_policy`: only a scheduled fire with both `saved_pipeline_id` and `tenant_id`) and
  carried through `initial_state`. Every `agents/pipeline/*.py` node keeps its
  `(state: PipelineState) -> PipelineState` signature — the override is just two more fields
  read off `state`, never a new parameter.
- **The analytical layer (Planner/Analyst/Science/Advisor) runs outside `PipelineState`**
  (unchanged from this module's own docstring), so `run_analyst`/`run_science`/`run_advisor`/
  `plan_analysis_tasks` each gained two optional trailing parameters instead, threaded through
  `pipeline_service.py`'s `run_gold_analysis`/`run_science_analysis`/`run_advisor_analysis`/
  `run_analysis_tasks` from the override `run_silver_pipeline` already resolved onto
  `silver_state` (no second DB round-trip).
- **`sources/document_source.py::load_document()`** — decided **in scope**: its `get_llm()`
  call is reached from `agents/pipeline/extractor.py::extractor_node`, inside the same graph
  run as every other `get_llm()` call site, so treating it as a silent exception to the
  override would be inconsistent for no real benefit. It gained the same optional
  `llm_provider_override`/`llm_model_override` parameters as the analytical layer (it isn't a
  graph node itself, `extractor_node` is).
- **Audit visibility (`log_action`) — two layers, matched to where the code already lives.**
  `agents/pipeline/*.py` nodes (`orchestrator_node`, `transformer_node`, and `extractor_node`
  when a `document` source runs) log a `llm_override_used` entry via `audit/logger.py::
  log_action` when an override is actually used — a real, persisted `PipelineState.audit_log`
  entry, visible via `save_run`. The analytical layer can't call `log_action` (no
  `PipelineState` to append to — the same reason it already used plain `logger.warning` for
  failures instead, see `pipeline_service.py`'s own comments) — it logs the same information
  via standard `logging` (`_log_llm_override_if_used`, tagged with the run's `run_id`) instead
  of inventing a second audit mechanism for one layer. Neither path fires when no override is
  configured — a deployment running only the global default sees zero new log volume.

**Residual gap, left open deliberately:** `audit/db.py`'s cost-persistence path
(`_write_analysis_row`'s `model_name = get_model_name()`, and `save_run`'s equivalent) still
reads the deployment-global model, not the resolved override — a pipeline that overrides to
Claude still has its `cost_usd` priced as if it ran on the deployment default. Wiring this
properly needs `_write_analysis_row` (currently only takes `run_id`/counts/tokens, no
`PipelineState`) to accept a resolved model name from its caller, which is a real, separate
change to `audit/db.py`'s cost path, not a corollary of this fix — left as an explicit,
tracked follow-up rather than folded in here.

## Consequences

- `MODEL_PRICING_USD_PER_MILLION_TOKENS` and `ALLOWED_MODELS_BY_PROVIDER` must be kept in sync
  going forward — a new allowlisted model with no pricing entry silently prices as `None`
  again. `test_every_llm_allowed_model_has_a_pricing_entry` guards this.
- `saved_pipelines` gains 2 nullable columns (migration 0016) — zero behavior change for every
  existing pipeline until a tenant explicitly calls `PUT /pipelines/{id}/llm-config`.
- `audit/db.py` grows by 2 functions, appended at the end of the file, touching no existing
  function body — a deliberate constraint for this parallel batch, documented above and in the
  functions' own docstrings, not an accident of style.
- Frontend picker is still a real, tracked gap against this sprint's own roadmap Definition of
  Done — see §5. Real per-pipeline execution wiring is **no longer** a gap — see §6: `get_llm()`
  now accepts an override, `PipelineState` carries it, and every real call site (including
  `sources/document_source.py`) respects it, with `log_action`/`logging` visibility into which
  provider actually ran. Cost tracking against the override is the one part of §6 still open —
  `audit/db.py`'s cost-persistence path prices against the deployment-global model regardless of
  an execution-time override.

## Alternatives considered

- **Fold `llm_provider`/`llm_model` into `UpdatePipelineRequest`/`patch_pipeline` instead of a
  dedicated endpoint pair** — rejected: that request's PATCH semantics treat `None` as "field
  omitted, unchanged" for every other field, which makes "explicitly clear the override back to
  the deployment default" inexpressible. A dedicated `PUT` endpoint with its own
  both-or-neither validator keeps that operation unambiguous.
- **Validate `llm_model` inside `get_llm()`/`audit/db.py` at read time instead of at the API
  boundary** — rejected: `get_llm()` is called on every pipeline run, in the hot path; the
  allowlist only needs to gate the one place untrusted client input enters the system (the
  `PUT` endpoint), matching `_validate_source_type`'s existing "validate once, at the API
  layer" posture.
