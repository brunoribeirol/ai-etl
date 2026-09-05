# Mega persona audit — 12 personas, full scope, execution-based

Owner: "Quero uma super mega ultra auditoria com personas... depois analisaremos as
considerações, implementaremos ou não." Full-scope run of the `.claude/skills/persona-audit.md`
protocol (all 12 personas, no subset), dispatched as 12 independent subagents. 4 of the 12 hit a
shared-Chrome-tab-group contention problem running in parallel (browser-driving personas fight
over the same tab) plus a session rate-limit; those 4 (Accessibility, LLM/Prompt Engineer, Data
Engineer, and partially Non-technical User) were retried solo and completed cleanly. Findings
below are pulled from the 12 individual reports in this same directory
(`docs/work/2026-09-04-persona-audit/<persona>.md`) — read those for full evidence trails.

**Non-goal, per the skill's own protocol**: no fixes as part of the audit itself, except one —
the date-parsing regression below directly contradicted a "fixed" claim this same session had
already made, and was corrected immediately rather than left inconsistent. Everything else here
is for the owner to triage next.

## P0 — fixed this session, live-verified

### Date-parse fix (PR #187) was itself still corrupting ISO dates — found by LLM/Prompt Engineer, confirmed, fixed (PR #191), re-verified live post-deploy

PR #187 (earlier this session) "fixed" pt-BR tenants getting ISO dates day/month-swapped by
comparing whether two `pd.to_datetime` readings *disagree*. The LLM/Prompt Engineer persona
reproduced live, twice, that this fix still corrupts ISO dates whenever day and month are both
≤ 12 (`2026-02-01` → `2026-01-02`) — the common case (~39% of days), not an edge case — because
`dayfirst=True` genuinely disagrees with the default reading in exactly that case, so the
"agreement check" picked the wrong reading. **Fixed properly in PR #191**: check the STRING
FORMAT directly (`pd.to_datetime(..., format="%Y-%m-%d", errors="coerce")` matching every
non-null value) before ever considering `dayfirst=True`. Verified against real pandas via new
unit tests (not just prompt text this time), **and** re-verified live post-deploy against
production with the exact reproduction case: uploaded `2026-01-01/02-01/03-01`, got back
unchanged — no swap. Status: **fixed, deployed, live-confirmed.**

Open sub-question from Data Engineer's DOCX test: a *mixed*-format date column (not pure ISO,
not pure day-first — genuinely different formats in the same column) got some valid values
nulled by pandas' format-inference behavior. Not the same bug, not yet verified against the new
fix — flagged for a follow-up check, not fixed this session.

## P0 — not fixed, owner already aware in principle, severity understated until now

### Sandbox escape reaches real command execution, and production's only active isolation is `multiprocessing` — Red Team

The `exec()` sandbox bypass via `().__class__.__base__.__subclasses__()` was already documented
as an accepted, known limitation (ADR-038/039). Red Team went further: reproduced it locally
with the project's exact `SAFE_BUILTINS`, escalated to `subprocess.CompletedProcess.__init__
.__globals__["run"]`, and **executed `whoami` for real** — arbitrary command execution, not just
`os.environ` leakage (the framing the code's own comments use). This runs on the `"process"`
backend, which is the default in production today because Railway doesn't run Docker-in-Docker
and Vercel Sandbox (the real Firecracker-microVM mitigation) is switched off by the owner's own
billing decision. **The risk ADR-038/039 accepted is currently running with its primary
mitigation inactive** — not a new bug, but a materially different severity read than "known,
accepted, mitigated."

## P0 — not fixed, genuinely new

### Real command-execution/currency bugs in LLM-generated code — Data Engineer, live-verified with runs

- **Excel multi-sheet always fails.** `_load_excel()` raises `ValueError` whenever a workbook has
  >1 sheet and no `sheet_name` is given — and the Orchestrator's plan schema has no `sheet_name`
  field at all, so there is no code path by which one ever gets supplied. The upload form
  advertises "Excel" as supported with no caveat; any realistic multi-sheet export fails 100% of
  the time.
- **BR-currency cleanup code is systematically broken.** Root-caused in live-generated code:
  `str.replace('R\$ ', '')` — `\$` is an invalid Python escape, so the literal backslash stays in
  the pattern and the `"R$ "` prefix is never actually stripped. Confirmed twice, independently,
  in two different files/agents (Transformer on a CSV — the whole `preco` column ends up null;
  Analyst on a DOCX table — crashes the entire analysis with `could not convert string to float:
  'R$ 5500.00'`). Systematic, not chance — same escape mistake, same currency format, two
  unrelated code-generation calls.
- **Nested (non-flat) JSON has no success path today.** Doesn't degrade to a stringified dict as
  hypothesized — it crashes the Transformer entirely (~12.5s, "Failed", no error surfaced in the
  UI).

## High — not fixed, genuinely new

- **File upload on `/app` is completely keyboard-inaccessible** (Accessibility). The real
  `<input type="file">` is `display:none`; the visible `<label>` doesn't respond to Enter/Space.
  Live keyboard-only Tab traversal confirmed focus skips straight over it.
- **Narrative hallucination with confident wrong numbers, on a data type this audit was built to
  catch** (LLM/Prompt Engineer). Science, given (already-corrupted-at-the-time) monthly data,
  stated a specific revenue figure and a specific month-over-month drop, both wrong versus the
  underlying table. `_validate_narrative_consistency`'s deterministic guard-rail checks the
  *last* numeric column by position, not the column the narrative actually names — it had no
  chance to catch this. Independent, secondary finding same run: Advisor gave a "high priority"
  recommendation off a 2-customer segment and a raw column named `profit_margin` that wasn't
  actually a computed margin, with no hedge for tiny N — same class of "semantic slip" the
  2026-08-24 audit already flagged as systemic and still unaddressed.
- **`drift_threshold_pct` has zero UI** (Product/PO). The only genuine "backend-complete,
  zero-UI" feature found this round — confirmed live, no field anywhere in the pipeline
  create/edit form; only reachable via a direct `PATCH /pipelines/{id}` call.

## Medium — not fixed

- **`/history`'s table has no table semantics** — 5 identical redundant `<a>` links per row, no
  `role="row"`/column context (Accessibility).
- **`localeToggle.switchTo` i18n key still leaks as both visible text and `aria-label`** on every
  page — flagged 11 days ago (2026-08-24 audit, "Alta"), still unfixed (Accessibility).
- **Navigation feels broken to a non-technical user**: menu highlights the wrong tab while
  content doesn't change; refreshing or deep-linking any non-home page bounces back to "Run an
  analysis"; clicking a History row is inconsistent (Non-technical User). *Caveat the persona
  itself couldn't rule out*: this ran during peak contention with other browser-driving personas
  sharing the same tab group — worth re-verifying once, in isolation, before treating as
  confirmed product bugs (distinct from the landing-page-jargon and model-picker findings below,
  which are pure content/copy, not navigation-timing-dependent, and don't need re-verification).
- **Landing page and in-app copy assume the reader already knows what a "pipeline"/"Silver/Gold/
  Science" is**, and the AI-model picker leaks raw internal engineering notes ("PR #109",
  "temperature-parameter fix") into a production decision screen (Non-technical User — this
  specific leak was independently seen and screenshotted earlier this same session, unrelated to
  the audit, so it's confirmed twice over).
- **"Billing" is only internal cost governance — no payment/monetization layer exists anywhere in
  the repo** (CTO/Founder). Not a bug, but the audit artifact's own "Cost, budget & billing"
  label invites the wrong reading for an outside audience (investor, banca).
- **RLS's `rolbypassrls=true` (ADR-032) protects against a leaked Supabase anon key, not an
  app-level bug that forgets a `tenant_id` filter** — the audit artifact's phrasing overstates
  the guarantee (CTO/Founder).

## Low / cosmetic — not fixed

- `tests/integration/test_quality.py` tests nothing that's actually integration — duplicates
  `tests/unit/test_quality.py` almost verbatim, no real DB/I-O (QA).
- Test-count claim in `CURRENT_STATE.md` ("1168 tests") doesn't match a bare `tests/unit`
  collection (1144) — near-certainly a unit-vs-unit+integration scope difference, not inflation
  (CTO/Founder).

## Fixed this session (small, unrelated to the above)

- One more unexplained `# type: ignore[no-any-return]` in `api/serialization.py:43`, missed by
  session 3's "commented every remaining one" pass (Tech Lead) — commented now.

## Confirmed still fine / nothing new (full detail in each persona's own file)

- **Blue Team**: secret redaction in `log_action()` (tested live with nested keys, not just
  read), RBAC coverage on every mutating route, `print()` confined to the CLI entrypoint — all
  confirmed, nothing new.
- **SRE**: Railway (4 services) and Vercel all healthy; LLM circuit breaker has real "provider
  down" test coverage; scheduled-pipeline alerting confirmed firing at exactly the 3rd
  consecutive failure via a dedicated test.
- **Red Team — SQL injection and RBAC**: both confirmed still closed, no new exploitable path
  (including the new MySQL/MongoDB destinations).
- **Academic Evaluator**: no new high-severity narrative/code mismatch; one vault doc
  (`evaluation-metrics.md`) is stale about a metric that's actually already instrumented.
- **Product/PO**: `GET /pipelines/{id}/llm-config`/`notification-config` are dead but harmless
  (data already comes through the list endpoint); 2026-08-24's audit is itself now stale about
  `admin/*`/`tenant/export` having no frontend consumer — both are consumed today.

## Already known, reconfirmed (not re-listed as new above)

Sandbox `exec()` bypass existence (not its full reach — see P0 above), single real tenant,
Vercel Sandbox off by billing decision, TCC case-study numbers predating Clerk/RLS/approvals/
multi-source — all previously documented in `docs/CURRENT_STATE.md` and reconfirmed accurate by
multiple personas independently.

## Process notes for next time

- **Don't run browser-driving personas in parallel against the same Chrome session.** This
  session's first parallel dispatch of all 12 hit real tab-group contention (multiple personas'
  navigation stealing each other's tabs) on top of an unrelated session rate-limit — both caused
  real failures. Sequential (or at least browser-persona-isolated) dispatch cost more wall time
  but produced clean, trustworthy results on retry.
- **The date-parsing bug survived two separate "fixes" from two separate sessions of this exact
  audit-and-fix loop**, both times because the fix was verified by reasoning/prompt-text
  assertions instead of an actual live re-run against the deployed app. The one thing that
  finally closed it for real was running the exact reported input against the exact deployed
  code and reading the actual output. Worth being the standing bar for any prompt-engineering
  fix in this codebase, not just this one.
