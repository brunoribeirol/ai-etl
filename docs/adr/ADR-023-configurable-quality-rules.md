# ADR-023: Operator-Configurable Quality Rules

**Status:** Accepted
**Date:** 2026-08-21
**Sprint:** 16 (post-TCC product roadmap)

## Context

`agents/quality.py` today runs a fixed set of deterministic checks — nulls, exact duplicates,
by-column logical duplicates, type mismatch against source schema, IQR outliers — the same
checks for every pipeline, chosen by the framework rather than by the operator. Roadmap
Sprint 16 (`artefact/product-roadmap-post-tcc.md`) asks for the operator to define their own
rules per saved pipeline (e.g. "`amount` is never negative", "`customer_id` is never null"),
which then run on every subsequent execution of that pipeline and show up in the same
`quality_report` shape the frontend (Sprint 7's Pipeline tab) already renders.

**Definition of done:** an operator defines a rule, it appears in the quality report of a
later run of the same saved pipeline, and it fails visibly when violated.

## Decision 1 — A whitelisted declarative DSL, not `exec()` of user code

This project's own non-negotiable rule (`CLAUDE.md`) bars `exec()` outside
`core/sandbox.py`. A custom quality rule is operator-authored, effectively untrusted input
persisted to the database and re-run unattended on every future scheduled fire — the exact
shape of input that must never reach `exec()`/`eval()`, sandboxed or not: even
`core/sandbox.py`'s restricted-builtins sandbox exists for LLM-*generated* Python that a human
already reviewed once per pipeline definition, not for a rule silently re-executed forever
on a cron schedule with no re-review step.

A rule is instead a plain, JSON-serializable dict interpreted by a fixed, whitelisted
dispatcher — no code, no string expression, no dynamic attribute access:

```json
{"column": "amount", "operator": "gte", "value": 0, "severity": "error", "name": "amount não pode ser negativo"}
{"column": "customer_id", "operator": "not_null", "severity": "error"}
```

Supported operators (`agents/quality.py::_CUSTOM_RULE_OPERATORS`): `not_null`, `gte`, `lte`,
`gt`, `lt`, `eq`, `ne` — each maps directly to one vectorized pandas comparison against the
column, mirroring the existing fixed checks' own all-pandas, no-LLM execution style (this
project's quality checks were never an LLM cost center — ADR-016's "por quê" note repeats
this — and a rule DSL should not become the first one). `value` is required for every operator
except `not_null`; `severity` defaults to `"error"` (a business rule violation is assumed
worth blocking unless the operator explicitly downgrades it to `"warning"`); `name` defaults to
an auto-generated label (`f"{operator} {column}"`) when omitted, for display.

**Alternatives considered and rejected:**

- **A second LLM pass validating the rule's intent against the data.** Rejected for this
  sprint: adds real LLM cost and non-determinism to a check family that has always been
  free and deterministic; also duplicates Sprint 18's own scope ("segundo passe de LLM
  revisando o resultado" is explicitly that later sprint's decision to make, for *output*
  validation, not input rules).
- **A restricted-`eval` expression string** (e.g. `"amount >= 0"`) via `pandas.eval` or a
  safe-eval library. Rejected: `pandas.eval`/`numexpr` accept a much larger surface than
  seven whitelisted comparisons (attribute access, function calls in some configurations,
  parser edge cases), and a new third-party safe-eval dependency is unjustified when the
  roadmap's own two worked examples ("never negative", "never null") are both single-column
  comparisons a tiny operator enum already covers completely. A future sprint can widen the
  DSL (e.g. a `regex` operator, or a `between` operator) without touching this decision.
- **Multi-column / cross-row rules** (e.g. "sum of X equals Y", "no two rows share Z"). Out of
  scope for this sprint — the roadmap's own two examples are both single-column, single-value
  rules; `_check_duplicates`/`_check_logical_duplicates` already cover generic cross-row
  duplication. Left as a documented limitation, not silently precluded (the dict shape can
  grow a `columns: list[str]` variant later without breaking existing single-`column` rules).

## Decision 2 — Persisted as a JSON column on `saved_pipelines`, not a new table

`quality_rules` (new `JSON` column, migration `0012`, `nullable=False`,
`server_default='[]'`) — a plain JSON array of rule dicts, mirroring `drift_threshold_pct`'s
own precedent (ADR-018): a per-pipeline configuration value that belongs to the pipeline's own
identity, not a history of its own. Unlike `saved_pipelines.consecutive_failures`/`last_status`
(ADR-020), this is not a derived cache of another table — the rule *definitions* have no other
source of truth; only their *violations* (computed fresh per run, folded into that run's
`quality_report`) are ephemeral, matching how the existing fixed checks already work (no
persisted check history separate from each run's own `quality_report`).

**Why not a normalized `pipeline_quality_rules` table (one row per rule)?** Considered and
rejected for this sprint: rules are always read and written as one atomic unit (the whole set,
via the pipeline's own `POST`/`PATCH`), never queried or updated independently of their parent
pipeline, and the roadmap's own scope note explicitly discourages new frontend/data-model
surface beyond what the existing Pipeline tab already renders. A JSON array is the smaller
change and matches this project's existing precedent of using `Text`/free-form columns for
data whose shape belongs to the application layer, not the schema (`saved_pipelines.spec`
itself is exactly this pattern already). Revisit if a future sprint needs to query "which
pipelines have a rule on column X" across tenants — not a need this sprint has.

## Decision 3 — Execution: threaded through `PipelineState`, not fetched inside the node

`agents/quality.py::quality_node` keeps its existing `(state: PipelineState) -> PipelineState`
contract and stays a pure function of its input state — no direct database access from inside
a LangGraph node, consistent with every other node in this graph (`extractor_node`/
`transformer_node` etc. all receive their inputs via state, not by querying the database
themselves). A new `PipelineState` field, `custom_quality_rules: list[dict[str, Any]]`
(default `[]`), carries the rule set in; `quality_node` runs `_check_custom_rules(df, rules)`
alongside the existing five check functions and folds its results into the same `checks` list,
same `{"check", "column", "severity", ...}` shape (extended per-entry with
`rule_name`/`operator`/`value`/`violation_count` — additive fields, `QualityCheck`'s frontend
type already has a catch-all index signature, so no breaking change to the existing renderer).

The fetch itself happens one layer up, in `services/pipeline_service.py::run_silver_pipeline`,
the same place `saved_pipeline_id` is already threaded through from `services/scheduler.py` →
`execution_queue.py` → here (ADR-017). When both `saved_pipeline_id` and `tenant_id` are set
(true exactly when this is a scheduled fire — `scheduler.py` always passes the pipeline's own
real `tenant_id` alongside its id, confirmed by reading `services/scheduler.py` before writing
this ADR, not assumed), `run_silver_pipeline` calls the existing `audit.db.get_saved_pipeline`
and passes its `quality_rules` into `initial_state()`. An avulso (`POST /runs`) run has no
saved pipeline to look rules up from and always runs with `custom_quality_rules=[]` — the
existing fixed checks only, unchanged behavior for every pre-Sprint-16 caller.

**Why not fetch inside `quality_node` directly (by adding `saved_pipeline_id` to
`PipelineState`)?** Rejected: it would make one LangGraph node the only one in the graph with
a live database dependency, breaking the "nodes are pure functions of `PipelineState`" property
this codebase relies on for testing every other node with a plain dict (see every existing
`tests/unit/test_quality.py` test, none of which mock a database connection) — and it
duplicates the tenant/pipeline resolution `run_silver_pipeline` already does for
`saved_pipeline_id` itself.

## Consequences

- An operator can express "column X is never negative" or "column Y is never null" (the
  roadmap's own two worked examples) today; a genuinely cross-row or cross-column rule is not
  expressible yet — documented limitation, not silently unsupported (`POST`/`PATCH /pipelines`
  validates the `operator` enum and rejects an unknown one with a 400, so a rule outside the
  current DSL fails loudly at definition time, not silently at run time).
- Rule violations default to `severity: "error"`, meaning by default a custom rule can block
  the pipeline (`route_after_quality` already routes to `END` on any `"error"` severity check,
  regardless of which check produced it) — the same blocking behavior the fixed null/type
  checks already have at their own error thresholds. An operator who wants a rule to be
  informational only sets `"severity": "warning"` explicitly.
- `quality_rules` only ever applies to a *saved* pipeline's *scheduled* fires and any avulso
  run the operator explicitly re-derives from that saved pipeline's spec later (not automatic —
  there is no linkage from an arbitrary `POST /runs` call back to a saved pipeline's rules).
  Rules for one-off, never-saved runs are out of this sprint's scope, matching the roadmap's
  own framing ("regras... persistidas por pipeline salvo").
- Migration `0012` to be verified locally (throwaway Postgres: upgrade head, `\d
  saved_pipelines` matches, downgrade -1 clean, re-upgrade clean) before merge — **not applied
  to production Supabase from this sprint alone**, same checkpoint discipline as every prior
  migration in this project.

## Related

- ADR-016 — `saved_pipelines` data model and the "quality checks are deterministic, not an LLM
  cost center" framing this ADR continues.
- ADR-018 — `drift_threshold_pct`, the precedent for a per-pipeline JSON/scalar configuration
  column rather than a new table.
- ADR-020 — the precedent for a JSON-shaped column threaded through `run_silver_pipeline` via
  `saved_pipeline_id`/`tenant_id`, and for keeping LangGraph nodes free of direct DB access.
- Vault: `artefact/product-roadmap-post-tcc.md`, Sprint 16.
