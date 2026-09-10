# 2026-09-09 — Remediation plan: full 12-persona audit findings

**Status: PLAN ONLY — nothing implemented.** Source of findings:
[`2026-09-08-full-persona-audit.md`](2026-09-08-full-persona-audit.md) (consolidated) +
`2026-09-08-persona-audit/*.md` (11 per-persona reports). Each finding's ID (C1, H1–H6,
M1–M17) matches the consolidated doc. Keep "found the problem" (the audit) and "decided how
to fix it" (this plan) and "did it" (a later session) as three separate, separately-approved
steps — the same discipline as the 2026-09-05 remediation plan.

## Objective

Close every open finding from the 2026-09-08/09 audit that isn't already an
honestly-documented accepted risk, ordered by **impact** (the audit's ranking), so the
project can honestly answer "yes" to the three readiness questions it currently answers "no"
to (external unsupervised use, and charging money) and strengthens the "yes" it already earns
(TCC defense).

## Non-goals

- Not re-running the audit or re-litigating "verified GOOD" items (SQL allowlist, RBAC
  coverage, ADR-040 RLS design, circuit breaker, dirty-data robustness, backup restore
  rehearsal).
- Not building the full billing system in this plan — that's a product track of its own
  (subscription vs usage-metered vs hybrid, Stripe hosted vs embedded). This plan lays out the
  decision gate and the *cheap* spend-safety fix (default budget cap) that shouldn't wait for it.
- Not writing the TCC monograph (vault, out of scope). The case-study *refresh* (M10) is in
  scope only as a repo artifact.
- Not a from-scratch prompt-architecture rewrite — every prompt fix is the smallest change that
  closes the specific finding.

## Cross-cutting rule this plan exists to enforce

**Every fix that changes LLM-generated-code behavior or locale-resolved output (H5, M3, M11)
must be live-verified against the deployed app with the exact reproduction input before being
marked done — not just reasoned about or covered by a text assertion.** This is not
boilerplate: this audit found H5 (progress-message locale) reproducing live *after* PR #221
was recorded as its fix, exactly the "passed a weak bar, still wrong" pattern that bit the
pt-BR date bug twice. Any item below touching a prompt or locale gets a live-verify step in its
own row, done *before* the PR is mergeable.

## Affected contracts

- `POST /runs` (`api/routers/runs.py::create_run`) **may gain** an optional `locale` Form param
  (H5, pending D4) — additive, backend falls back to `get_locale(tenant_id)` when absent.
- `audit/models.py` + a new migration: `monthly_budget_usd` **may gain a server default** (H2,
  pending D2) — additive; existing rows backfilled or left NULL per the chosen default.
- `.github/workflows/ci.yml`: the required `check` job **gains Postgres/Redis services** (or the
  `e2e`/integration job becomes required) so RLS + sandbox-containment tests stop self-skipping
  (H4) — CI-config only, no app change.
- `audit/logger.py::_sanitize` keyword set widens + `audit/db/runs.py::_write_json` gains a
  sanitize pass (M4) — behavior change to *persisted* redaction only, no signature change.
- Prompt-only changes: `advisor.py` (M3 consistency/hedge), `transformer.py`/`quality.py` (M11
  numeric-coercion flag). No node signature changes.
- Frontend additive: cost-estimate UI on the run form (M5), result-tab relabels (M12),
  `useAsyncData` extraction (M17), theme aria-label + hardcoded-string i18n (M7).
- No breaking route or state-contract changes anywhere. Migrations: at most one (M2 budget
  default), additive.

## Wave 0 — owner decision gates (no code until you decide)

These are blocked on a call only you can make, not on investigation.

| # | Finding | The decision | My read |
|---|---|---|---|
| **D1** | C1 — Clerk dev instance on public prod | (a) Buy a domain + migrate Clerk to a **production** instance; (b) keep dev instance **and roll signups back to vetted-only** until (a) | If the product is genuinely open to strangers, (a) is non-optional and blocks charging money. If it's still portfolio/demo, (b) is honest and free. Pick based on b/c readiness intent. |
| **D2** | H2 + billing | Two linked calls: (i) **monetization model** (subscription / usage-metered / hybrid; Stripe hosted vs embedded) — the big product track; (ii) the **default `monthly_budget_usd`** for a new tenant — small, shippable now independent of (i) | Ship a conservative default cap now (Wave 1) regardless of (i). Don't let the billing decision hold spend-safety hostage. |
| **D3** | H1 — sandbox RCE/file-read, root container, trigger crossed | (a) Vercel Sandbox Pro (real isolation, recurring cost, declined 2026-08-31); (b) **drop root (`USER` in Dockerfile) + restrict FS/egress** on the `process` backend at the OS level; (c) accept + roll signups back to vetted | (b) is the best cost/benefit: closes the "root reads any file / reaches network" blast radius without recurring SaaS cost, and doesn't chase the losing `__subclasses__` arms race. Do (b) + revisit (a) when a paying tenant is real. |
| **D4** | H5 — which locale is authoritative for run-time messages | (a) **UI cookie wins** — frontend sends its active locale with `POST /runs`, backend uses it for progress + narrative; (b) tenant DB `users.locale` wins, and the toggle must reliably sync it | (a) matches what the user actually sees and is what PR #221 *meant* to deliver. Recommend (a). |
| **D5** | M10 — case-study refresh scope | Re-run how many scenarios, which model(s), commit which artifacts? | Recommend: re-run all scenarios on the current default model + one Claude model, commit per-scenario run JSONs (not just the aggregate table), and add scenario 4/5. Owner's call on how much, given TCC timeline. |

## Wave 1 — critical/high, unblocked or cheap (do first)

| # | Finding | Fix shape | Effort | Live-verify? |
|---|---|---|---|---|
| **W1-1** | H3 — no LICENSE | Add `LICENSE` (MIT) at repo root; add `license = {text="MIT"}` to `pyproject.toml`; bump `version` off `0.1.0` to reflect reality | Tiny (~15 min) | No |
| **W1-2** | H4 — security-control tests skip in required CI | Add `postgres-test`/`app-postgres-test`/`redis` services to the required `check` job (so `test_tenant_isolation_rls` + `test_audit_persistence` + `test_alembic_migration` run), and either build the sandbox image in CI or make the `e2e` job a **required** check; then tighten branch protection (≥1 review, `enforce_admins`, code-owner reviews) | Medium (~half day; watch CI minutes) | No — verify by confirming the tests run (not skip) in a PR's check output |
| **W1-3** | H6 — SECURITY.md + ADR-003 stale | Rewrite `SECURITY.md`'s code-execution section to describe the real one-site, timeout-enforced, env-scrubbed, seccomp'd sandbox; mark ADR-003 superseded-by ADR-007/046 with a header note; fix the two dead file paths | Small (~1-2h, docs only) | No |
| **W1-4** | H2 — uncapped self-serve spend | Set a conservative server default for `monthly_budget_usd` on tenant creation (migration + `get_budget_status` treating unset-new as the default); surface it in onboarding | Small–medium (~half day incl. migration) | No — verify a new tenant is capped |
| **W1-5** | H5 — EN-UI tenant sees PT | Per D4(a): thread the frontend's active locale through `POST /runs` → `run_full_analysis` → progress/narrative, falling back to `get_locale`. Add a test pinning "UI locale wins over DB locale." | Medium (~half day, frontend + backend) | **Yes** — run on an EN-UI/pt-BR-DB tenant, confirm progress + narrative are EN |
| **W1-6** | H1 — sandbox blast radius | Per D3(b): add `USER` (non-root) to `Dockerfile`; block egress/FS for the sandbox process user at the OS level. If D3=(c), instead: update ADR-032/SECURITY to record the crossed trigger + roll signups back to vetted | Medium–large (real infra) or Small (doc + config) depending on D3 | **Yes** (Linux container) — re-run the Red Team file-read/RCE PoC, confirm blocked |

## Wave 2 — medium: correctness & trust in the core output

| # | Finding | Fix shape | Effort | Live-verify? |
|---|---|---|---|---|
| **W2-1** | M3 — Advisor secondary figures wrong + revenue≠demand | Prompt: require one consistent revenue methodology (Σ price×qty) cited identically across products; add a hedge that revenue ≠ demand/volume. Add a Gold-vs-narrative numeric-consistency check (extends the ADR-026 sanity check to cross-check cited figures against the Gold table) | Medium (~half day, prompt + sanity-check code + tests) | **Yes** — re-run the audit's `vendas_sujas.xlsx` revenue question, confirm consistent figures |
| **W2-2** | M4 — redaction gaps | Widen `_SENSITIVE_KEYWORDS` (`connection`, `dsn`; consider a value-pattern check for `sk-`/DSN shapes); apply `_sanitize` in `_write_json` before persisting state + to the `error` column; add the missing **error-path** variant of the tenant-secret regression test | Medium (~half day) | No |
| **W2-3** | M5 — `/runs/estimate` zero-UI | Build the cost-preview UI the endpoint's own docstring specifies: call `POST /runs/estimate` on dataset-shape change, render `estimated_cost_usd` + `basis` before "Analyze data" | Small–medium (~half day) | No — verify estimate renders + matches |
| **W2-4** | M11 — bad-type numeric silently → 0 | Transformer/Quality: when numeric coercion drops a non-numeric value, keep it NaN (not 0) and flag via `is_incomplete`/a quality warning, so units math isn't silently corrupted | Small–medium (~2-4h, prompt + quality check + test) | **Yes** — re-run with `quantidade="cem"`, confirm not silently 0 |
| **W2-5** | M8 — `/health` is a stub | Add a `/health/ready` (or deepen `/health`) that pings DB + Redis and returns non-200 on failure; point Railway's healthcheck at it | Small (~2-3h) | No — verify 503 when a dep is down |

## Wave 3 — medium: ops, security hygiene, doc/decision-record accuracy

| # | Finding | Fix shape | Effort |
|---|---|---|---|
| **W3-1** | M1 — backup gaps | Add `if: failure()` alerting (Slack/email) to `db-backup.yml`; add an S3 lifecycle/retention rule on `_system/db-backups/`; extend tenant erasure (or document) to reach or expire prior dumps; **add GitHub as a sub-processor** in `docs/compliance/sub-processors.md` | Small–medium (~half day) |
| **W3-2** | M2 — SAST advisory | Re-enable CodeQL (repo is public → free; uncomment triggers in `codeql.yml`); make Semgrep a **required** check to match its documentation | Small (~1-2h) |
| **W3-3** | M6 — stale ADR statuses + CHANGELOG | Add `**Update (YYYY-MM-DD): Accepted — migration NNNN applied**` lines to the 7 "Proposed" ADRs (014/016/017/018/019/020/022); refresh `CHANGELOG.md` from v1.0.0→now; sync `pyproject` version | Small (~2-3h, docs) |
| **W3-4** | M7 — hardcoded PT strings | Route rate-limit/budget/alert/auth error strings + `theme-toggle.tsx:57` aria-label through the locale mechanism (backend: the same catalog pattern as `progress_messages.py`; frontend: next-intl) | Small–medium (~half day) |
| **W3-5** | M13 — narrow local gate | Widen ruff `select` to `["E","F","I","N","W","B","S","UP","RUF"]`, `--fix` the 19 dead `# noqa`; narrow bandit `skips` to `["B101"]` so B102 (`exec_used`) is enforced locally via the existing per-site `# nosec` | Small (~2-3h, one lint-config PR) |
| **W3-6** | M14 — dependency drift | Raise floors to what's tested (`langgraph>=1.2`, `openai>=2.0`, etc.), add upper caps on the LLM stack, sync `CLAUDE.md`'s stack line | Small (~2h) |
| **W3-7** | M15 — ADR-039 unamended | Add an `**Update:**` recording the HTTP-403 Hobby-plan block + the declined Pro upgrade, so the ADR doesn't read as "one env var away" | Tiny (~15 min) |
| **W3-8** | M16 — log_action asymmetry | Cheap: document that `audit_log` is the Silver trail and name what covers the BI layer. Or real: add a state-free `log_analysis_action()` so `audit_log` spans all 9 agents | Small (doc) or Medium (code) |

## Wave 4 — evaluation, non-tech polish, low

| # | Finding | Fix shape | Effort |
|---|---|---|---|
| **W4-1** | M10 — case-study refresh | Per D5: re-run scenarios on current model(s), commit per-scenario run JSONs + refreshed `tabela-resultados.md`, add scenario 4/5, and write a sentence explaining the all-`warning` quality result | Medium (real runs, $ + time) |
| **W4-2** | M9 — no automated LLM-quality test | Add a lightweight golden/eval harness (a few fixed inputs with asserted properties on the output: correct top answer, internally consistent figures) — or at minimum formalize + commit the manual protocol | Medium–large (its own track) |
| **W4-3** | M12 — jargon in result tabs | Relabel Silver/Gold/Advisor tabs with user-facing names (technical name secondary) | Small (~2-3h) |
| **W4-4** | M17 — duplicated fetch effect | Extract one `useAsyncData(fetcher, deps)` hook (or adopt SWR/TanStack Query); collapse 8 components, delete 8 suppressions | Medium (~half day) |
| **W4-5** | LOW batch | Currency BR-format in narrative; dedup consistency; `/test-connectivity` viewer-spend guard; orchestrator short-circuit doc-comment; alembic `0013` header note; `__main__` print exemption in CLAUDE.md; Terraform "never applied" banner; CHANGELOG/branch-protection claim | Tiny each |

## Suggested execution order

1. **Wave 0 decisions D1–D5** — several Wave 1/2 items are shaped by them (D2→W1-4, D3→W1-6, D4→W1-5, D5→W4-1). D1 gates whether (b)/(c) readiness is even the goal.
2. **Wave 1** — W1-1 (LICENSE) and W1-3 (SECURITY doc) are instant, no decision. W1-2 (CI) unblocks trustworthy green. W1-4/5/6 follow their decisions.
3. **Wave 2** (trust in output).
4. **Wave 3** (hygiene; W3-2/W3-5 are quick security wins; independent, any order).
5. **Wave 4** — W4-1/W4-2 are the TCC-evaluation track (schedule against the December deadline).

Each wave ships as its own PR (or a few small PRs for independent items), same branch → PR →
CI-green → squash-merge discipline. The live-verify step (W1-5, W1-6, W2-1, W2-4) happens
*before* the PR is mergeable, not after.

## Acceptance criteria

- Every prompt/locale item: a new/updated unit test on the actual algorithm **plus** a real run
  against the deployed app reproducing the finding's exact input, corrected output observed
  directly (not inferred).
- Every frontend item: `npm run build` clean, manually exercised in a real browser against live.
- `make check` clean before every merge. After W1-2, CI must actually *run* (not skip) the RLS +
  sandbox-containment tests — confirm in the check output.
- After W3-5, `make lint` fails on a stray `exec()` outside the sandbox (test it).
- `docs/CURRENT_STATE.md` gets one entry per wave/PR, and any reopened "fixed" claim (H5) is
  explicitly re-marked, not silently overwritten.

## Open questions before starting

Wave 0's D1–D5 all need your call. D1 (Clerk) and D3 (sandbox) gate anything security/public-
facing; D2 (billing model) is the big product decision but the default cap (W1-4) can proceed on
a conservative value now; D4 (locale authority) shapes the one reopened "fixed" bug; D5 scopes
the TCC evaluation refresh. Waves 1–4 that aren't tied to a decision I can start on your
go-ahead without further per-item questions.
