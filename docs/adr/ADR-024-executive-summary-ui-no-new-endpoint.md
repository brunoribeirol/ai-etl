# ADR-024 — Executive summary UI reuses existing endpoints (no new backend surface)

**Status:** Accepted
**Date:** 2026-08-21
**Deciders:** Bruno Ribeiro

---

## Context

Sprint 18 (Vault: `artefact/product-roadmap-post-tcc.md`, "UI executiva dedicada") closes a
real gap the dual-ICP framing (`saas-potential.md` §2) already named: the frontend built in
Sprints 6/7 (and extended by Sprint 17's "Histórico comparável") is entirely shaped for the
technical operator — tabs literally named "Pipeline" and "Código", navigation keyed by
`run_id`. A non-technical executive consumer has no surface at all today; they'd have to
open the same run detail page and either ignore or be confused by both tabs.

The roadmap's own scope note is explicit that this should be presentation-only: "consome os
mesmos endpoints, só apresenta diferente." This ADR exists to record whether that held up
after investigation, per `.claude/specs/sr-standard.md`'s requirement that every SaaS-roadmap
item get an architecture decision recorded — even when the answer is "there is no real
trade-off here," following the precedent Sprint 22 already set (dirty-data robustness shipped
with no ADR because every fix stayed inside existing connector modules, no new connector
architecture — `sr-standard.md`'s own criterion for when an ADR is warranted).

## Investigation

Read before writing any frontend code, per this project's own standing rule:

1. **`src/ai_etl/services/digest.py` (Sprint 14/ADR-018)** — the executive content this
   sprint needs to surface (drift findings + Advisor summary/recommendations) is pure
   formatting over data that already exists elsewhere; `build_digest()` itself only produces
   email/Slack-shaped output (subject/text/html/Slack blocks), not something a web page can
   render directly.
2. **`services/alerting.py::check_drift_and_notify`** — confirmed (and Sprint 14's own
   `CURRENT_STATE.md` entry says explicitly) that triggered drift findings are **not
   persisted**: they're computed against the previous completed run, formatted, delivered
   best-effort, then discarded. There is no `drift_findings` table or column to read back.
   A literal "show the persisted digest" endpoint is therefore not possible without a new
   table — out of this sprint's stated scope (presentation only).
3. **`GET /runs/{run_id}` (`api/routers/runs.py` → `audit.db.load_full_result`)** already
   returns everything a single run's executive summary needs: `advisor.summary`,
   `advisor.recommendations`, KPI-shaped fields (`rows_loaded` via `state.transformed_data`,
   `tokens`, cost via the run's own persisted `cost_usd`). This is the same endpoint the
   existing technical run-detail page already calls — no new read path.
4. **`GET /pipelines/{id}/history` (Sprint 17/ADR-017 → `audit.db.list_pipeline_run_history`)**
   already returns a tenant-scoped, oldest-first time series of one saved pipeline's KPIs
   (`rows_loaded`, `cost_usd`, `total_tokens`) — comparing the two most recent entries gives
   the same "what changed since last time" signal Sprint 14's drift check computes, without
   needing to re-run `core/drift.py` server-side or persist anything new. Sprint 17's own
   `pipeline-history.tsx` already proved this exact pattern (client-side diff over two
   `GET /runs/{run_id}` calls) works and needs no backend diff endpoint.
5. **`GET /pipelines` / `GET /pipelines/{id}`** already expose `business_question` — the
   field this sprint's "navigate by business question, not by technical `run_id`" requirement
   needs for the executive entry point.

**Conclusion**: every input the executive summary page needs is already exposed by an
existing, already-tenant-scoped, already-read-only endpoint. No new database column, no new
table, no new API route, no new query in `audit/db.py`. This sprint is pure frontend
composition + a plain-language reformatting of data three earlier sprints (13, 14, 17) already
made queryable — the same shape of "no new architecture" finding Sprint 22 recorded, which is
why this ADR documents the absence of a trade-off rather than a decision between alternatives.

## Decision

**No new backend endpoint, table, or column.** The executive summary UI is implemented
entirely in `frontend/` as a new route namespace (`/resumo`, `/resumo/[pipelineId]`),
composing three existing calls per page: `GET /pipelines/{id}` (identity/business question),
`GET /pipelines/{id}/history` (latest vs. previous run KPIs, client-computed delta — the same
technique `pipeline-history.tsx` already uses), and `GET /runs/{run_id}` (Advisor
summary/recommendations for the latest completed run). Scoped to **saved (recurring)
pipelines only** — an avulso (`POST /runs`) execution has no `business_question`-first
identity to navigate by and no comparable-history endpoint; those runs keep using the existing
technical `/historico/[runId]` page unchanged.

The one real design choice made (not a trade-off between competing architectures, but worth
recording so it isn't silently assumed): drift-style "what changed" is derived **client-side**
from the two most recent `GET /pipelines/{id}/history` entries rather than by calling
`core/drift.py`'s exact threshold logic. This means the executive page's "what changed" signal
is informational (any KPI movement, shown with a plain-language direction indicator) rather
than the same triggered/not-triggered boolean `services/alerting.py` uses to decide whether to
send a digest at all. Acceptable here — the exec page's job is "help someone understand what
happened," not "re-decide whether an alert should have fired" — but flagged so a future sprint
persisting real drift-finding history (see `CURRENT_STATE.md`'s Sprint 14 "not done" note) can
replace this client-side approximation with the real thing without it being a silent behavior
change no one chose on purpose.

## Consequences

- Zero migration, zero ADR-numbered schema decision to make (this document exists to record
  *that*, not a schema).
- The executive page's "what changed" is an approximation of Sprint 14's real drift check, not
  the same computation — documented above, not silent.
- A pipeline with no `business_question` set has nothing executive-shaped to show beyond raw
  KPIs (no Advisor ever runs without one) — the UI handles this explicitly rather than showing
  an empty Advisor section.
- Scoped to saved pipelines only; avulso runs are out of this sprint (same technical page as
  before). If a future sprint wants an executive view for avulso runs too, it would need a
  business-question-first identity for them, which doesn't exist today — a real follow-up
  question, not addressed here.

## Related

- ADR-018 — drift detection + digest delivery (the content this UI presents).
- ADR-017 — comparable run history (the endpoint this UI's "what changed" reuses).
- ADR-016 — scheduled pipelines data model (`business_question` field, the navigation key).
- `docs/CURRENT_STATE.md` Sprint 22 — the ADR-skip precedent this document follows the shape of
  (unlike Sprint 22, this sprint *does* get an ADR, per `sr-standard.md`'s requirement for every
  SaaS-roadmap item — this ADR's content is the "why nothing architectural changed," not a
  silent skip).
