# AI-ETL — Full 12-Persona Audit (2026-09-08/09)

**Protocol:** `.claude/skills/persona-audit.md`, full scope (all 12 personas), single Claude
Code session (Opus), no paid cloud review. Each persona executed against the **real system**
(ran `make check`, ran the deployed app, made real LLM calls, ran real exploit PoCs, read
code line-by-line). This is the third run of this protocol (prior: 2026-08-24, 2026-09-04).

**Question this audit answers:** is AI-ETL finished — and finished *for what*? It is a CESAR
School TCC (2026) that is also a professional portfolio piece; the bar is "defensible in front
of a recruiter, tech lead, academic board, or investor."

## Process notes (real, worth recording)
- **Code-only personas were dispatched as parallel subagents; all 7 hit a claude.ai session
  rate-limit (HTTP 429) and terminated.** Two (Tech Lead, CTO/Founder) had written their full
  reports before dying; the other five (Red Team, Blue Team, SRE, QA, Academic Evaluator) had
  not. Rather than re-dispatch into the same wall, the remaining five were completed **inline**
  by the lead session after the limit reset — more reliable than re-spawning. Browser personas
  (Data Engineer, LLM/Prompt Engineer, Non-technical, Accessibility, Product/PO) were driven
  sequentially by the lead session (the documented no-parallel-Chrome rule).
- **Every UI observation was cross-checked against the real code mechanism before being
  written** (per the protocol's screenshot≠mechanism rule). This correctly killed one candidate
  finding (the "unnamed info button" — it has an sr-only label; the tree just didn't surface it).
- **`make check` was run once, centrally, from scratch:** EXIT=0 — unit+integration **1199
  passed / 14 skipped / 95.11 % coverage**; e2e **5 passed** (15.86 s, LLM mocked); bandit
  **No issues** (12,112 LOC); pip-audit **No known vulnerabilities**. Lint/format/mypy(strict)
  all green. The codebase's CI health is genuinely excellent.

## Per-persona reports (full detail in `2026-09-08-persona-audit/`)
| Persona | File | One-line |
|---|---|---|
| CTO/Founder | `cto-founder.md` | Impressive artifact, not a business: can't take money, no license, dev-mode auth, uncapped spend |
| Tech Lead | `tech-lead.md` | Code is defensible; the *docs understate the code*; local lint gate narrower than it looks |
| Red Team | `red-team.md` | Sandbox RCE + file-read live-reproduced; SQLi properly closed |
| Blue Team | `blue-team.md` | RBAC strong; redaction key-name-only & never applied to persisted state |
| SRE | `sre.md` | Circuit breaker real; `/health` is a stub; backups silent-fail with no retention |
| QA | `qa.md` | Suite honest in comments; the two security controls' tests don't gate CI; no LLM-quality test |
| Academic | `academic-evaluator.md` | ADRs exemplary; evaluation chapter partial/dated/non-reproducible |
| Product/PO | `product-po.md` | Parity strong; `POST /runs/estimate` is backend-complete/zero-UI |
| Data Engineer | `data-engineer.md` | Dirty-data fixes hold live; bad-type value silently coerced to 0 |
| LLM/Prompt | `llm-prompt-engineer.md` | Headline answer correct; secondary revenue figures wrong; revenue↔demand conflation |
| Accessibility + Non-tech | `accessibility-and-nontechnical.md` | Theme aria-label hardcoded PT; result tabs use Silver/Gold jargon; PT leaks on EN UI |

---

## Prioritized, deduplicated findings (by IMPACT, not ease)
"⭐ N personas" = independently found by N personas — a stronger signal, not redundancy.
Status: **OPEN** (documented, not fixed — the audit's default). Nothing was fixed this session
(see "The one allowed fix" note below).

### CRITICAL
| # | Finding | Evidence | Personas | Status |
|---|---|---|---|---|
| **C1** | **Public production authenticates on a Clerk *development* instance.** Caps user growth, fails any enterprise security review, relaxed security posture. Not in SECURITY.md / risk-register / SOC2 (which asserts auth ✅ with no caveat). | `curl ai-etl.vercel.app` → `pk_test_c2FjcmVkLXNw…` (dev key, `*.clerk.accounts.dev`) | CTO | OPEN |

### HIGH
| # | Finding | Evidence | Personas | Status |
|---|---|---|---|---|
| **H1** | **Sandbox escape → RCE + arbitrary host file read**, live-reproduced on the default `process` backend. Env-scrubbing holds, but seccomp (ADR-046) blocks only `execve` — **file read stays open even on seccomp'd Linux**, and the container runs as **root** (no `USER` in Dockerfile). ADR-032's own "real isolation before self-serve signups" trigger is **crossed** while the mitigation (Vercel Sandbox) is billing-disabled. | PoC: `whoami`→`brunoribeiro`, `/etc/passwd` read; `os.environ`→empty | ⭐3 Red Team + CTO + Tech Lead | OPEN (documented accepted risk, trigger now crossed) |
| **H2** | **No default budget cap → every self-serve tenant gets unlimited monthly spend** on the owner's own OpenAI/Anthropic keys (bounded only by 10 runs/hr). `monthly_budget_usd` is nullable, no server default; ADR-019 cap is opt-in and nobody opts a stranger in. | `audit/models.py:44`; `0009_tenant_budget_cap.py` | CTO | OPEN |
| **H3** | **No `LICENSE` file** in a public repo whose README shows an MIT badge linking to `(LICENSE)` (404); `pyproject` version still `0.1.0`, only `v1.0.0` tag. Public repo = "all rights reserved" by default. Cheapest credibility fix in the repo. | `git ls-files \| grep -i licen` → empty; `README.md:6` | CTO | OPEN |
| **H4** | **The two headline security controls' tests never run in required CI.** RLS tenant-isolation (`test_tenant_isolation_rls.py`, needs PG:5435) and sandbox containment (`test_sandbox_docker.py`) **self-skip** in the only required `check` job (declares only mysql/mongo services); the `e2e` job that has PG runs only `tests/e2e/` and **isn't required**. Branch protection: **0 reviews, admins not enforced**. A regression in `tenant_scope()` merges green. | `ci.yml:38-61,90-91,120-146`; `gh api …/protection` | ⭐3 QA + CTO + Blue Team | OPEN |
| **H5** | **English-UI tenant still sees Portuguese** progress messages ("⚡ Executando pipeline Silver…") and narrative — the *exact* symptom PR #221 claimed fixed, reproduced live. Root cause (verified in code): progress/narrative locale = tenant DB `users.locale` (default `pt-BR`) via `get_locale()`, an **independent source** from the next-intl UI cookie (en-US). PR #221 localized to the DB locale, not the UI the user sees. | Live run 755432de; `pipeline_service.py:167,199`; `progress_messages.py:82`; `run-form.tsx` sends no locale | Live + Non-tech + LLM/Prompt | OPEN — see note below |
| **H6** | **`SECURITY.md` + `ADR-003` describe a sandbox that hasn't existed since Sprint 2** — "three `exec()` sites" (there's one), two dead file paths, "timeout not enforced" (it is, `sandbox.py:556`), "Follow-up open" (closed by ADR-007). Public repo; this is the first file a security reviewer opens. Makes the system look worse than it is. | `SECURITY.md:25-41`, `ADR-003` vs `sandbox.py:209`; own `CURRENT_STATE.md:2122` admits it | ⭐2 Tech Lead + CTO | OPEN |

### MEDIUM
| # | Finding | Personas | Status |
|---|---|---|---|
| **M1** | GitHub is an **undisclosed sub-processor** (nightly `pg_dump` of PII on a GH runner); backup workflow has **no failure alerting, no S3 retention**; **right-to-erasure doesn't reach backups**; GH disables cron after 60 days idle → silent RPO=∞. | ⭐2 CTO + SRE | OPEN |
| **M2** | **CodeQL disabled** on an expired "private repo needs GHAS" rationale (public since 2026-08-30 → CodeQL is free); Semgrep/e2e/frontend are **non-blocking** → the whole SAST layer is advisory. Three docs still assert the expired reason as current. | ⭐2 Tech Lead + CTO | OPEN |
| **M3** | **Advisor output unreliable in the secondary numbers:** headline correct (Caneta R$24,691.20) but Mochila cited 250 (actual 500) and Cadernos 179.80 (actual 899) — inconsistent revenue method; a recommendation rests on a wrong number; "high revenue" conflated with "high demand." | LLM/Prompt | OPEN |
| **M4** | **Redaction is key-name-only and never applied to persisted state** (`_write_json` doesn't sanitize; `connection_string`/`dsn`/raw `sk-…` leak); tenant-secret leak guard covers only the happy path, not the error path (`extractor.py:91` puts raw `{e}` into unsanitized `state["error"]`). | Blue Team | OPEN |
| **M5** | **`POST /runs/estimate`** (FinOps cost estimation) is backend-complete with **zero UI**, despite its own docstring specifying the UX. (Would also soften H2.) | Product/PO | OPEN |
| **M6** | **7 ADRs still "Proposed"/"not applied to production"** for shipped, migrated features (014/016/017/018/019/020/022); **CHANGELOG frozen at v1.0.0** (2026-08-22) while ~130 PRs + 16 ADRs landed. ADR log misstates ~15 % of its own records. | ⭐2 Tech Lead + CTO | OPEN |
| **M7** | **Hardcoded Portuguese** in rate-limit/budget/alert/auth error strings (`execution_queue.py:212`, `auth_service.py`, `health_alerts.py`, …) and the **theme-toggle aria-label** (`theme-toggle.tsx:57`) — same i18n-leak class as H5. | ⭐2 CTO + Accessibility | OPEN |
| **M8** | **`/health` is a static `{"status":"ok"}`** — no DB/Redis/LLM dependency check; a dead dependency still returns 200 and Railway keeps routing. | SRE | OPEN |
| **M9** | **e2e mocks the LLM** + prompt regression is manual-only (ADR-029) → **no automated test of agent-output quality anywhere**; README "real stack" overstates. | ⭐2 QA + Academic | OPEN |
| **M10** | **Case-study evaluation is partial:** 3 of 4+ scenarios, every run `quality=warning`, single model, dated 2026-06-23, data gitignored (not reproducible from repo). | Academic | OPEN |
| **M11** | **Bad-type numeric value silently coerced to 0** (`quantidade="cem"`→0) — data loss with no `is_incomplete`-style flag. | Data Engineer | OPEN |
| **M12** | **Result tabs use medallion/agent jargon** (Silver/Gold/Advisor) for a non-technical audience. | Non-tech | OPEN |
| **M13** | **Local gate narrower than it looks:** 19 dead `# noqa` (rules not in ruff `select`); bandit **B102 (`exec_used`) skipped repo-wide** → the "exec() only in sandbox" non-negotiable has **zero enforcement in `make check`** (Semgrep catches it in CI only). | Tech Lead | OPEN |
| **M14** | **Dependency floors 2-3 majors behind reality** (`langgraph>=0.2`→1.2.6, `openai>=1.50`→2.43); only `pandas` capped; `CLAUDE.md` repeats the stale floors. | Tech Lead | OPEN |
| **M15** | **ADR-039 never amended** after Vercel Sandbox was billing-blocked — reads as "one env var away" when it's blocked behind a declined Pro upgrade. | CTO | OPEN |
| **M16** | **`log_action` asymmetry:** Analyst/Science/Advisor/Reviewer emit **0** `log_action` calls — `audit_log` covers Silver only, while docs claim end-to-end auditability. | Tech Lead | OPEN |
| **M17** | **8 components** hand-roll the same `set-state-in-effect` fetch pattern (missing `useAsyncData`/SWR abstraction). | Tech Lead | OPEN |

### LOW (documented in persona files)
`CHANGELOG`/branch-protection claim mismatch · Semgrep documented as blocking but non-required
· currency formatting mixes BR symbol + US separators · dedup inconsistency across identical
rows · `/llm/test-connectivity` triggers paid LLM call at `viewer` level · orchestrator lacks
the `state.get("error")` short-circuit (correct, but contradicts CLAUDE.md's "every node") ·
alembic `0013` gap (chain intact) · `print()` in untested `__main__.py` · Terraform never
applied (honestly labeled) · Starlette/Celery third-party deprecation warnings.

### Verified GOOD (the verdict rests on these too)
`make check` genuinely green at 95.11 % · no secrets committed, `.env` gitignored · Streamlit
fully retired (regression-pinned) · `exec()` confined to sandbox · SQL allowlist robust (real
prior exploit closed) · ADR-040 RLS is real defense-in-depth (per-txn GUC, fails closed) ·
ADR-042 backup work is exemplary (6 documented failures, real restore rehearsal) · multi-
provider LLM real and live-tested (OpenAI + Anthropic) · dirty-data robustness holds live
(currency/dates/multi-sheet/incomplete-row) · Agentic BI produces the correct headline answer
on messy input · `docs/compliance/` is auditor-shaped and self-aware · LangGraph contract clean
across 5 nodes · `/llm/test-connectivity` UI live-verified ("Working 1921ms").

---

## The one allowed fix — deliberately NOT taken, with reasoning
The protocol permits fixing exactly one thing during an audit: a finding that **directly
contradicts a "fixed" claim** in `CURRENT_STATE.md`. **H5 qualifies** — PR #221 (2026-09-08) is
recorded as fixing "an English-UI tenant watched 'Executando pipeline Silver…' scroll past,"
and that exact symptom reproduced live today.

It was **documented, not fixed**, for two protocol-consistent reasons:
1. **The correct fix is cross-cutting and cannot be live-verified in-session.** It requires the
   frontend to send its active UI locale with `POST /runs` and the backend to prefer it over
   the DB `users.locale` for progress/narrative — a frontend + backend change needing a Vercel
   deploy to verify. The protocol's hard rule (learned from the pt-BR date bug that "passed a
   weak bar and was still wrong" twice) is that a fix to claimed-fixed behavior must be
   **live-verified with the exact input** before being called done. Shipping an unverifiable fix
   would repeat exactly the mistake that rule exists to prevent.
2. **Root cause needs an owner decision:** which locale source is authoritative for run-time
   messages — the UI cookie the user sees, or the tenant's stored `users.locale`? That's a
   product call, not a mechanical fix.

`CURRENT_STATE.md` is **not** being edited to claim H5 fixed; it is flagged here as a reopened
item for the owner's separate remediation session.

---

## FINAL VERDICT (no hedging)

**a) Ready for a TCC board to evaluate? — YES.** The system is real and over-delivered: a
working multi-agent LLM ETL + Agentic-BI pipeline, 47 genuine ADRs, 1199 tests at 95 %, live in
production, with a documentation culture that repeatedly caught its own false assumptions in
writing. A board will accept the artifact. It **will** press on two soft spots, so close them
first: (1) the **evaluation chapter** (M10 — partial scenarios, all-`warning`, single/dated
model, non-reproducible data) and (2) the **doc drift** that makes the system look sloppier than
it is (H6, M6). Add the `LICENSE` (H3) before anyone browses the public repo.

**b) Ready for an external user to test unsupervised? — NO.** Four things a real unsupervised
user hits today: an **English UI that speaks Portuguese** mid-run (H5), **uncapped spend on the
owner's API keys** (H2), a **dev-mode identity provider** (C1), and a **root container with an
open sandbox file-read** the moment signups are self-serve (H1). Supervised demo: fine. Hand the
URL to a stranger: not yet.

**c) Ready to charge money? — NO, and not close.** There is **zero billing code** anywhere, no
`LICENSE`, dev-mode auth, uncapped spend, and — most damning for a product whose paid value *is*
the recommendations — **the recommendations can contain wrong numbers** (M3: correct headline,
wrong Mochila/Caderno figures). You cannot charge for advice built on arithmetic you haven't
made trustworthy, delivered by a system that can't take payment or legally be licensed.

**d) The 3 things that most need to happen, by impact (not ease):**
1. **Close the "impressive system vs. company" gap.** In one bundle: real payment path
   (billing), a `LICENSE`, production Clerk, and a **default budget cap** for new tenants. None
   is a hard engineering problem; together they are the entire distance to "product." (C1, H2,
   H3 + billing)
2. **Make the security posture match "public/self-serve."** The ADR's own precondition — real
   isolation *before* self-serve signups — is unmet: file-read is open on a root container, and
   the RLS/containment tests that would catch a regression don't gate CI. Either land real
   isolation (or drop root + restrict FS) **and** make those tests required, or roll signups
   back to vetted. (H1, H4)
3. **Make the core output — and the docs describing it — trustworthy.** The Advisor's secondary
   numbers are wrong and there's no automated agent-quality test to catch that (M3, M9); and the
   security/ADR docs understate or misstate the real (strong) system (H6, M6). The product's
   value and its credibility both live here.

**One-line bottom line:** *the engineering is finished and defensible; the product is not —
and the gap is almost entirely commercial, locale, and trust wiring, not core engineering.*
