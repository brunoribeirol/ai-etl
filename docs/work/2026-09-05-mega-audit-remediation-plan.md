# 2026-09-05 — Remediation plan: mega 12-persona audit findings

**Status: PLAN ONLY, nothing implemented yet.** Owner: Bruno Ribeiro (decisions, especially
Wave 0) + Claude (execution once each wave is approved).

## Objective

Close out every open finding from `docs/work/2026-09-04-mega-persona-audit.md` (12-persona
audit, 2026-09-04) that isn't already fixed. 2 items from that doc are already resolved (the
pt-BR date-parse bug, PR #191, live-verified; one stray `# type: ignore`, PR #192) and are not
repeated here.

## Non-goals

- Not re-running the audit itself, or re-litigating findings already marked "confirmed still
  fine" in the consolidated doc (SQL injection, RBAC, infra health, secret redaction).
- Not deciding the sandbox-isolation budget question (Wave 0, item 1) — that's the owner's
  billing call, this plan only lays out the options and cost of each.
- Not touching the TCC case-study re-run or monograph writing — explicitly out of scope per
  standing instruction.
- Not attempting a from-scratch redesign of any agent's prompt architecture — every fix here is
  the smallest change that closes the specific finding, not a broader rewrite.

## Cross-cutting rule this plan exists to enforce

**Every fix that changes LLM-generated-code behavior (items 2, 3, 4, 6, 7) must be live-verified
against the deployed app with the exact reported reproduction case before being marked done —
not just reasoned about or covered by a prompt-text assertion.** This is not boilerplate
caution: the pt-BR date bug was "fixed" twice this session and wrong both times, and the only
thing that caught the second failure was running the exact input against the real deployed code.
Any wave below touching a prompt gets this step explicitly in its own checklist, not assumed.

## Affected contracts

- `core/pipeline_plan_schema.py::PipelinePlanSource` gains an optional `sheet_name` field (item
  2) — additive, existing plans without it are unaffected (`Optional[str | int]`, default
  `None`, same fallback behavior `_load_excel` already has).
- `agents/pipeline/orchestrator.py`'s prompt gains new instructions (item 2's sheet enumeration,
  item 4's nested-JSON guidance) — prompt-only, no schema break for existing callers.
- `agents/analysis/science.py::_validate_narrative_consistency` (item 6) changes its column-
  selection logic — internal, no public signature change.
- `agents/analysis/advisor.py`'s prompt gains hedging instructions (item 7) — prompt-only.
- `agents/pipeline/transformer.py`'s prompt gains currency-cleanup guidance (item 3) — prompt-
  only, same file already touched twice this session for the date fix; same discipline applies.
- Frontend: `pipelines-manager.tsx` gains a `drift_threshold_pct` field (item 8, already a valid
  API field, additive); `run-form.tsx`'s file input markup changes for keyboard access (item 5);
  `history` page's row markup changes for table semantics (item 9); `en-US.json`/`pt-BR.json`
  gain/fix a few keys (items 10, 11).
- No backend route signatures change. No migration needed.

## Wave 0 — owner decision gates (no code until you decide)

These aren't blocked on investigation — they're blocked on a call only you can make.

| # | Item | The actual decision | My read |
|---|---|---|---|
| 1 | Sandbox RCE (Red Team) | (a) Turn on Vercel Sandbox Pro — real isolation, recurring cost, you already declined once; (b) Accept the risk explicitly as-is (single real tenant = you, today); (c) Cheaper partial mitigation short of full container isolation — see options below | I'd default to (b) + revisit when a second tenant is real, same framing the CTO/Founder persona suggested for due-diligence answers. Not proposing (a) again unprompted. |
| 12 | "Billing" section wording | Relabel "Cost, budget & billing" to something like "Cost governance" in anything shown externally (pitch materials, future audit artifacts) | This is a copy change in materials you control outside this repo, not app code — flagging so it doesn't quietly stay mislabeled, not proposing I rewrite your pitch deck. |
| 13 | RLS wording in audit materials | Same kind of fix — the guarantee `rolbypassrls=true` actually gives is narrower than "backstops every tenant_id filter" | Same as above — a wording fix, your call on where/whether to apply it. |
| 14 | Navigation instability (non-technical-user persona) | Re-verify in an isolated browser session (no other agent contending for the tab) before treating as a confirmed bug | Recommend doing this *before* Wave 3, cheap (~15 min), avoids spending real frontend-fix effort chasing what might have been audit-tooling contention, not a product bug. |

**Item 1, partial-mitigation options if you want something short of full Vercel Sandbox Pro**
(for discussion, not a recommendation to build any of these without you picking one):
- Run the `"process"` backend under a lower-privilege OS user with `subprocess`/network egress
  blocked at the OS level (e.g. a dedicated Railway service user, `iptables`/`nftables` egress
  deny) — closes the "arbitrary command execution reaches the network/filesystem" part Red Team
  demonstrated, doesn't need Vercel at all. Real infra work, not trivial, but no recurring SaaS
  cost.
- Strip the specific `__subclasses__()`-reachable path narrower (e.g. actively scrub
  `subprocess`/`os`-reachable classes from the sandbox's inheritable object graph before `exec()`
  runs) — this is a known-losing arms race against Python introspection (there are other
  `__subclasses__()`-reachable sinks besides `subprocess.CompletedProcess`), so I would not
  recommend this as the actual fix, only as a stopgap if (a) and the egress-block option are both
  off the table for now.

## Wave 1 — critical, code fixes (do first, in this order)

| # | Item | Fix shape | Effort | Live-verify required? |
|---|---|---|---|---|
| 2 | Excel multi-sheet always fails | `PipelinePlanSource.sheet_name: str \| int \| None` (schema); Orchestrator prompt enumerates real sheet names (already available at plan-generation time) and picks/asks; **also** fix the upload-time preview path (`_dataframe_from_upload` → `auto_generate_spec`) to default to the first sheet with an explicit note in the generated spec ("workbook has N sheets, used the first: '<name>'") instead of raising before the pipeline even starts | Medium (~half day: schema + prompt + upload-preview default + frontend sheet-count hint if you want one) | Yes — upload a real 3-sheet `.xlsx`, confirm it completes instead of erroring, confirm the *right* sheet's data comes through |
| 3 | BR-currency regex-escape bug | Add explicit currency-cleanup guidance + a correct canonical example to the Transformer prompt (same mechanism as the date fix, same file) — the wrong pattern (`str.replace('R\$ ', '')`) needs an explicit "ALSO WRONG" callout the way the date prompt now has, since this is exactly the kind of subtle escaping mistake an LLM repeats across unrelated runs | Medium (~2-3h: prompt text + tests exercising the corrected pattern against real pandas, same style as `test_transformer.py`'s date tests) | Yes — re-run Data Engineer's exact `vendas_br_edge.csv` reproduction, confirm `preco` survives with real numeric values |
| 4 | Nested JSON crashes the Transformer with no visible error | Two independent fixes, do both: (a) Transformer prompt guidance for flattening nested dict/list columns (`pd.json_normalize`-equivalent, no `import` available so needs a pandas-only pattern) so nested JSON gets an actual success path; (b) **regardless of (a)'s outcome**, surface a real error message in the UI when a Transformer attempt exhausts retries — today it's "Failed" with nothing else, which is a separate, smaller, always-worth-doing fix independent of whether nested JSON itself ever gets full support | (a) Medium-large (~1 day, genuinely new capability, needs several shapes of nested JSON tested); (b) Small (~1-2h, frontend + `pipeline_service.py` error propagation) | Yes for (a) — Data Engineer's `pedidos_aninhado.json`. (b) is verifiable by forcing any failure and checking the UI shows something. |

## Wave 2 — high severity

| # | Item | Fix shape | Effort | Live-verify required? |
|---|---|---|---|---|
| 5 | File upload keyboard-inaccessible | Either give the real `<input>` a `sr-only`/clip-path style instead of `display:none` (keeps it focusable, visually hidden), or add a `keydown` handler (Enter/Space → `.click()`) to the visible `<label>` | Small (~1-2h) | No LLM involved — verify with a real keyboard Tab traversal, same method Accessibility persona used |
| 6 | `_validate_narrative_consistency` checks the wrong column | Change column selection from `numeric_cols[-1]` (last by position) to actually matching which column the narrative text names, or fall back to the task's declared primary value/target column if one is threaded through | Medium (~half day — needs a real matching strategy, plus new unit tests with a narrative that names a non-last column) | Recommended — re-run a case shaped like the LLM/Prompt Engineer persona's `tiny_trend.csv`/`misleading_column.csv` and confirm the guard-rail actually flags a bad narrative this time |
| 7 | Advisor no small-N/unverified-column hedge | Add explicit prompt instructions: hedge or downgrade priority when a cited segment's N is small (e.g. <5), and avoid treating a column name (`profit_margin`, `revenue`) as verified semantics without checking it's plausible | Medium (~half day, prompt + a couple of regression tests asserting the hedge language appears in the prompt) | Yes — same reproduction dataset as item 6, check the actual generated recommendation text |
| 8 | `drift_threshold_pct` no UI | Add a labeled number input to the pipeline create/edit form in `pipelines-manager.tsx`, wire into the existing `POST`/`PATCH` payload (field already accepted server-side) | Small (~1-2h) | No — straightforward form field, verify by creating a pipeline with a non-default value and confirming it persists |

## Wave 3 — medium severity

| # | Item | Fix shape | Effort |
|---|---|---|---|
| 9 | `/history` table has no semantics | Wrap each row's fields in real `<table>`/`role="row"` markup, or collapse the 5 redundant `<a>`s into one row-level link with the rest as static text | Small (~2-3h) |
| 10 | `localeToggle.switchTo` untranslated key | Add the missing translation key to `en-US.json`/`pt-BR.json` — this is the actual known-11-days-stale item, likely a one-line miss in the i18n message file | Tiny (~15 min) |
| 11 | Jargon/internal notes leaking into production UI | Landing page copy pass (plain-language framing instead of "Bronze → Silver → Gold" pipeline jargon on first view); strip PR numbers/internal fix notes from the model-picker's user-facing description strings (keep them in a changelog/ADR instead) | Small-medium (~half day, mostly copywriting, not logic) |
| 16 | Mixed-format date column | Investigate first (not yet confirmed against PR #191's new strict-ISO-first logic) — the strict `format="%Y-%m-%d"` pre-check only helps when a column is *purely* ISO; a column mixing ISO and non-ISO strings still falls to the old `errors="coerce"` default parse, which infers format from the first value and can still drop valid rows in other formats. May need a per-value (not per-column) parsing pass | Investigate: ~1h; fix if confirmed real: Medium (~half day) |

## Wave 4 — low / cosmetic

| # | Item | Fix shape | Effort |
|---|---|---|---|
| 15 | `tests/integration/test_quality.py` duplicates unit tests | Delete the file (logic already covered in `tests/unit/test_quality.py`), or rewrite it to exercise something that actually varies with real infra (e.g. a `quality_checks` list coming from a real generated plan) — deletion is enough to stop the mislabeling | Tiny (~15-30 min) |

## Suggested execution order

1. **Wave 0, item 14** first (cheap, unblocks whether Wave 3's navigation fix is even real work).
2. **Wave 1** (critical, in the table's order — Excel and currency are independent of each
   other, nested-JSON's error-surfacing sub-fix (4b) can land even before full support (4a)).
3. **Wave 2**.
4. **Wave 3** items 9/10/15 (independent, small, any order) — item 11 and item 16 need their own
   small investigation/copy pass, do those last within the wave.
5. **Wave 4**.
6. **Wave 0, items 1/12/13** whenever you're ready to decide — none of them block any other wave.

Each wave should ship as its own PR (or a few small PRs per wave for the independent items),
same branch → PR → CI-green → squash-merge discipline as the rest of this session, with the
live-verification step called out above done *before* a PR touching LLM-generated-code behavior
is considered mergeable, not after.

## Acceptance criteria (per item, restated compactly)

- Every Wave 1/2 item that touches a prompt: a new/updated unit test exercises the actual
  algorithm (not just prompt text) where one is extractable (mirrors `test_transformer.py`'s
  date tests), **plus** a real run against the deployed app reproducing the original finding's
  exact input, with the corrected output observed directly (not inferred).
- Every frontend item: `npm run build` (includes type-check) clean, manually exercised in a real
  browser session against the live app.
- `make check` clean before every merge, same bar as the rest of this session.
- `docs/CURRENT_STATE.md` gets one entry per wave (or per PR, owner's preference) recording what
  was verified and how, matching this session's existing style.

## Open question for you before I start

Wave 0 needs your call on item 1 (and optionally 12/13) before I touch anything security-related
— I won't implement any of the partial-mitigation options above without you picking one (or
telling me to leave it alone for now). Everything in Waves 1-4 I can start on your go-ahead
without further decisions needed per item.
