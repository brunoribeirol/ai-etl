# ADR-036: Locale and internationalization (backend narrative + frontend UI)

**Status:** Accepted
**Date:** 2026-08-22
**Sprint:** 25 (Fase 2 product roadmap, "Nível Big Tech" — deferred original-roadmap item, picked
up now on Bruno's explicit go-ahead, 22/08/2026)

## Context

The roadmap's Sprint 25 definition of done: *"um tenant configurado como en-US produz
datas/moeda no formato certo em todo o pipeline, incluindo a narrativa gerada."* Bruno's scope
confirmation narrows and extends that: a dynamic, functional EN/PT-BR toggle covering the whole
landing page, the whole app, and the LLM-generated narrative — not just UI labels.

Three things are 100% Portuguese-hardcoded today:

1. **`agents/pipeline/transformer.py`'s date-parsing heuristic.** The prompt already tries the
   default (month-first) `pd.to_datetime` parse and falls back to `dayfirst=True` if more than 5%
   of values become `NaT` — a locale-agnostic heuristic for *unambiguous* dates. It has no
   opinion at all for the *ambiguous* case (`"01/02/2024"` — valid either way, zero `NaT` under
   both readings): it silently keeps the month-first result regardless of the tenant, which is
   wrong for a pt-BR tenant's day-first data whenever ambiguity hides the mismatch.
2. **`agents/analysis/{planner,analyst,science,advisor}.py`'s prompts** hardcode "in Portuguese"
   instructions for every LLM-generated string (`narrative`, chart titles/axis labels, the
   Advisor's `summary`/recommendation fields, even the Planner's sub-question phrasing).
3. **`frontend/src/`** has zero i18n infrastructure — every string is inlined Portuguese JSX.

## Decision

### 1. Locale is per-tenant, on `users`, not per-pipeline

`users.locale` (migration `0020`, `NOT NULL DEFAULT 'pt-BR'`) — same shape as
`users.retention_days`/`monthly_budget_usd` (ADR-019/ADR-035): a single column, no history
table, self-service via `GET`/`PATCH /tenant/locale` (`editor`-gated for the `PATCH`, same trust
boundary as `PATCH /tenant/retention`).

**Why tenant, not per-`saved_pipeline`** (unlike ADR-031's LLM provider/model override, which
*is* per-pipeline): a pipeline's provider/model is a cost/quality tradeoff an operator might
reasonably vary run-to-run. Locale is not — in this product, `tenant_id` *is* the account (see
`users.id`'s "Clerk user_id, not a UUID" comment), and every persona interacting with one
tenant's pipelines, saved views, and generated narratives expects one consistent language and
date/currency convention, not a per-pipeline mix. A per-pipeline override would also orphan the
frontend: the landing page, the dashboard shell, `/comecar`, `/historico` — everything outside a
single pipeline's page — has no `saved_pipeline_id` to key off at all. Precedent: this mirrors
ADR-025/ADR-035's existing tenant-wide config columns, not ADR-031's pipeline-scoped one.

**Why `NOT NULL DEFAULT 'pt-BR'`, not nullable "no override" like `llm_provider`:** locale always
has a value — there is no meaningful "unset" state the way "no LLM override, fall back to env
var" is meaningful for ADR-031. Every existing tenant reads back `'pt-BR'` after the migration,
which is the exact behavior every one of them already has today — zero behavior change until a
tenant explicitly calls `PATCH /tenant/locale`.

Only two locales are supported at launch: `pt-BR` and `en-US`, enforced by
`core/locale.py::resolve_locale()` (unknown/malformed input silently falls back to `pt-BR`,
mirroring `resolve_locale`'s soft-fail contract elsewhere in this codebase — e.g.
`_saved_pipeline_row_to_dict`'s "coerce, don't reject" style) and validated at the API boundary
(`PATCH /tenant/locale`, 422 on anything else) before being persisted — same
"allowlist validated once at the boundary, never re-validated at read time" pattern as
`core.llm.validate_provider_and_model` (ADR-031 §2).

### 2. Threading follows ADR-031 §5's provider/model override exactly

`core/locale.py` is the single source of truth for locale metadata:

```python
LOCALE_METADATA: dict[str, LocaleMetadata] = {
    "pt-BR": {"language_name": "Portuguese (Brazil)", "dayfirst": True,
              "date_format_hint": "DD/MM/YYYY", "currency_symbol": "R$",
              "currency_code": "BRL", "timezone": "America/Sao_Paulo"},
    "en-US": {"language_name": "English (US)", "dayfirst": False,
              "date_format_hint": "MM/DD/YYYY", "currency_symbol": "$",
              "currency_code": "USD", "timezone": "America/New_York"},
}
```

`PipelineState` gains `locale: str` (never `Optional` — `initial_state(..., locale="pt-BR")`
defaults it, matching every existing caller's unchanged behavior). `services/pipeline_service.py
::run_silver_pipeline` resolves it once via `audit/db/locale.py::get_locale(tenant_id)` whenever
a real `tenant_id` is present (unlike `custom_quality_rules`/`approval_policy`/the LLM override,
this does **not** require a `saved_pipeline_id` too — locale applies to every run a tenant makes,
avulso or scheduled, exactly the same way `run_id`/`tenant_id` themselves do) and carries it into
the graph via `initial_state`, then forwards it out of `PipelineState` into the
Planner/Analyst/Science/Advisor calls the same way `run_full_analysis` already forwards
`llm_provider_override`/`llm_model_override` out of `silver_state` as plain parameters (ADR-031
§5) — this layer runs outside the LangGraph/`PipelineState`, so it was already threaded as
parameters, not state.

Every prompt template's hardcoded `"in Portuguese"` becomes a `{language_instruction}`
placeholder filled from `core/locale.py::narrative_language_instruction(locale)` (e.g. *"Write
the narrative in English (US, en-US)."*). `agents/pipeline/transformer.py`'s prompt gains a
`{date_parse_hint}` block: for a day-first locale it instructs the LLM to attempt
`dayfirst=True` **first**, falling back to the default month-first parse only if that produces
more `NaT`s (the mirror image of today's month-first-first heuristic) — the tie-break-by-NaT-count
fallback is unchanged, so a genuinely ambiguous file with zero `NaT` either way now resolves
toward the tenant's own convention instead of always defaulting to US month-first.

`agents/analysis/science.py::_validate_narrative_consistency`'s trend-word lists
(`_INCREASE_WORDS`/`_DECREASE_WORDS`) gain their English equivalents rather than being switched
per locale — checking both unconditionally is simpler than threading locale into that one
validator too, and a false match against the "wrong" language's word list is harmless (the
validator only *rejects* on a **contradiction** between a detected direction and the numbers; an
extra vocabulary never manufactures a contradiction that wasn't already a real bug).

**Currency/timezone:** threaded into the same prompts as formatting guidance (state the tenant's
currency symbol/code and date format when the LLM must render a monetary value or a date in
narrative text) rather than a hard runtime enforcement layer — there is no structured
currency/date field in `GoldResult`/`ScienceResult`/`AdvisorResult` to reformat mechanically
today; the narrative is free text the LLM composes. This is a real limitation, not a full
implementation, and is called out explicitly in Consequences below.

### 3. Frontend: `next-intl`, App Router route groups, one message catalog per locale

No i18n library existed in `frontend/` before this sprint (confirmed by inspection — zero
`next-intl`/`i18n` references anywhere in `frontend/src` or `package.json`). `next-intl` is the
current de-facto standard for the Next.js App Router (server component support, typed message
catalogs, App Router route-group locale segments) — chosen over rolling a custom
context/localStorage-only solution because the requirement is explicitly "the landing page
entire, the app entire," which needs server-rendered locale-aware routes (so a shared link or a
crawler gets the right language on first paint), not just a client-side toggle repainting
strings after hydration.

`messages/en.json` / `messages/pt-BR.json` hold the UI string catalog (landing + app shell +
navigation + forms + toasts). The toggle persists the choice (cookie-backed, read by
`next-intl`'s middleware on every request, so it survives a reload and a shared link) and is
reachable from any screen via the shared app/marketing shell, not a page-local control.

The backend narrative toggle is a **separate axis** from the frontend UI toggle: the frontend
locale cookie is a *display* preference (which catalog to render chrome in) and does not, by
itself, change what language the Advisor writes in — that is `users.locale`, set once via
`PATCH /tenant/locale` (surfaced in a settings control that also flips the frontend cookie in the
same action) so a tenant configuring "English" gets both an English UI and an English narrative
from one control, without conflating "how this browser renders buttons" with "what language a
persisted, cross-session, billing-relevant tenant setting is in."

## Consequences

**What ships fully localized:**
- `users.locale` config + `GET`/`PATCH /tenant/locale`, threaded through the Silver graph and the
  Planner/Analyst/Science/Advisor analytical layer exactly like ADR-031's provider/model override.
- Transformer's date-parsing heuristic now prefers the tenant's own day-first/month-first
  convention on ambiguous input instead of always defaulting to US month-first.
- Analyst/Science/Advisor/Planner narrative, chart titles/axis labels, and Advisor summary/
  recommendations are generated in the tenant's configured language.
- Frontend: `next-intl` infrastructure, EN/PT-BR message catalogs, a locale toggle reachable from
  every screen and persisted via cookie, landing page and the app shell/navigation fully wired.

**Explicitly out of scope / known limitations for this sprint** (flagged here rather than
silently shipped as if complete, same convention as ADR-031 §5's own "explicitly out of scope"
section):
- Currency/date **values** inside `gold_df`/`predictions_df`/charts are not mechanically
  reformatted per locale — only the LLM's own narrative *text* is steered via prompt guidance.
  A DataFrame column of raw numbers/dates is unaffected; this is a text-generation nudge, not a
  formatting engine.
- Frontend page-by-page coverage is reported in the PR description, not claimed 100% here —
  see that PR for exactly which routes/components are fully message-catalog-driven vs. still
  carrying literal Portuguese strings, and a proposed follow-up split if anything remains.
- No third locale beyond `pt-BR`/`en-US` — `core/locale.py::SUPPORTED_LOCALES` is a fixed
  2-entry tuple; adding a third is a small, additive change (new `LOCALE_METADATA` entry + a new
  message catalog) but is not pre-built here.
