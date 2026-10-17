# Persona — CTO / Founder

**Date:** 2026-09-08
**Repo state:** `main` @ `31ce865`, clean tree, public repo `brunoribeirol/ai-etl`
**Scope:** due-diligence red flags; the gap between what the documentation claims and
what the code actually is. Audit only — nothing was fixed, no source touched.

---

## Method (what I actually ran and read)

**Read in full:** `CLAUDE.md`, `README.md`, `SECURITY.md`, `docs/compliance/*.md` (all 5),
`docs/CURRENT_STATE.md` (top ~400 lines in full + targeted greps across all 2161),
`Dockerfile`, `Makefile`, `.gitignore`, `.gitguardian.yaml`, `pyproject.toml`,
all 9 files in `.github/workflows/`, `.github/CODEOWNERS`,
`src/ai_etl/audit/connection.py`, `tests/integration/test_tenant_isolation_rls.py`.

**ADR sweep:** extracted the declared `Status:` line from all 47 ADRs in one pass; then
read in depth ADR-003, ADR-015, ADR-029, ADR-032 (all 4 Decisions), ADR-038, ADR-039,
ADR-040, ADR-042, ADR-043, ADR-046, ADR-047; grepped every ADR for
`Follow-up` / `Open question` / `Revisit trigger` headings and cross-checked the named
modules exist (`core/scheduling.py`, `core/drift.py`, `api/routers/budget.py`,
`services/secrets_service.py`, `alembic/versions/0006`–`0022`).

**Commands actually executed (output used as evidence):**
- `gh repo view brunoribeirol/ai-etl --json visibility,isPrivate,licenseInfo,…`
- `gh api repos/brunoribeirol/ai-etl/branches/main/protection`
- `gh api repos/brunoribeirol/ai-etl/rulesets`
- `git ls-files | grep -i licen` (empty), `git tag`, `git log --first-parent -25`
- `curl -s https://ai-etl.vercel.app/ | grep -oE "pk_(test|live)_[A-Za-z0-9]{0,12}"`
- `grep -rn "exec(" src/ai_etl/` and targeted greps for hardcoded PT strings,
  personal identifiers, AWS account ids, `rolbypassrls`, `_system/db-backups`
- Read the already-running `make check` log at
  `…/scratchpad/make-check.log` (did **not** re-run it, per instruction).

**Not used:** browser tools (other personas own that), Railway/Vercel MCP env reads
(would mean reading production secrets), any DB connection to production.

---

## Findings

### CRITICAL

**1. The live, publicly-open production app authenticates users on a Clerk *development
instance*.**

Evidence — verified against the live site, not the repo:
```
$ curl -s https://ai-etl.vercel.app/ | grep -oE "pk_(test|live)_[A-Za-z0-9]{0,12}"
pk_test_c2FjcmVkLXNw
$ echo "c2FjcmVkLXNw" | base64 -d
sacred-sp
```
`pk_test_…` is a Clerk **development** instance key; the decoded payload is a
`sacred-sp….clerk.accounts.dev` dev subdomain. `docs/CURRENT_STATE.md:1132` records the
decision — *"Clerk stays in development mode — owner declined to buy a dedicated domain
just for this project. Production Clerk migration … remains explicitly deferred, not
scheduled."* — but that entry is dated **2026-08-23**, before the product was opened to
self-serve external signups (2026-09-06, per `SECURITY.md:56-60`). Clerk development
instances are documented by the vendor as not for production use (shared `accounts.dev`
domain, a hard user cap, a relaxed security posture, the dev-browser handshake this
repo's own `CURRENT_STATE.md:1623` documents biting it twice).

Why it matters: this is the first question a technical acquirer or an enterprise buyer's
security review asks, and the answer is currently "our identity provider is in dev mode."
It also caps the product's growth ceiling below any commercial threshold.

Already documented as a known/accepted risk? **Partially, and staler than it looks.**
It is a one-line decision in a 2161-line living doc, made under a different set of facts.
It appears in **none** of: `SECURITY.md`, `docs/compliance/risk-register.md` (8 rows, no
Clerk row), `docs/compliance/soc2-readiness-assessment.md` — whose CC5 table asserts
*"Authentication | ✅ | Clerk, JWT verified locally via JWKS (ADR-006)"* with no caveat.
That control claim is materially incomplete.

---

### HIGH

**2. No `LICENSE` file exists, in a public repo whose README displays an MIT badge.**

```
$ git ls-files | grep -i licen        →  (empty)
$ gh repo view … --json licenseInfo   →  {"licenseInfo": null, "isPrivate": false}
```
`README.md:6` renders `[![License](…badge/license-MIT-green)](LICENSE)` — the badge links
to a 404. `README.md:141-143` says "## License / MIT". `pyproject.toml` declares no
`license` field either.

Why it matters: a public repo with no license is "all rights reserved" by default —
legally, nobody may use, fork, or contribute. For a portfolio piece this is the
single cheapest credibility fix in the repo and its absence is immediately visible to
any recruiter or OSS reader who clicks the badge.

Documented as known? **No.**

**3. Production containers run as root, while the sandbox bypass is confirmed real.**

`Dockerfile` (read in full, 40 lines) has no `USER` directive — one image serves the
FastAPI API, the Celery worker, and celery-beat on Railway. `SECURITY.md:61-76` and
ADR-046 confirm a Red Team persona reproduced **real arbitrary command execution**
(`subprocess.run(["whoami"])`) through the `"process"` backend's introspection bypass, and
that the seccomp filter closes only `execve`/`execveat`, Linux-only, explicitly **not**
the bypass itself. Anything that escapes via a non-`execve` gadget therefore runs as
uid 0 inside the production container.

Documented as known? **Only in passing.** `docs/CURRENT_STATE.md`'s 2026-09-06 narrative
notes *"this project's `Dockerfile` has no `USER`, so production runs as root"* as an
aside inside a story about a failed `RLIMIT_NPROC` fix. It is not in `SECURITY.md`, not
in `risk-register.md` row 1 (which describes the bypass without mentioning the uid), and
not in the SOC2 assessment.

**4. Public self-serve signup with no default spend cap on the owner's own LLM keys.**

`src/ai_etl/audit/models.py:44` — `Column("monthly_budget_usd", Float, nullable=True)`;
`alembic/versions/0009_tenant_budget_cap.py:40` adds it `nullable=True` with no server
default. `services/execution_queue.py::get_budget_status` returns
`cap_usd=None, exceeded=False` whenever it is unset. So **every new self-serve tenant is
created with an unlimited monthly budget**, bounded only by
`RATE_LIMIT_MAX_RUNS = 10` / `RATE_LIMIT_WINDOW_SECONDS = 3600`
(`execution_queue.py:67-68`) against the operator's own OpenAI/Anthropic credentials.
ADR-019's budget cap is real and works — it is simply opt-in per tenant and nobody opts a
stranger in.

Documented as known? **No.** No risk-register row. ADR-019 was written when the product
had one known tenant; the framing was never revisited when signups opened.

**5. The tests protecting the two headline security controls never execute in CI.**

`SECURITY.md:136-149` calls ADR-040's RLS tenant isolation *"a real, verified (see
`tests/integration/test_tenant_isolation_rls.py`) second layer of defense."* That file
(lines 34-61) self-skips unless Postgres is reachable at
`localhost:5435`. `.github/workflows/ci.yml`'s `check` job — **the only required status
check on `main`** — declares services `mysql-test` (3307) and `mongodb-test` (27018)
only. The `e2e` job *does* run `app-postgres-test` on 5435, but its only step is
`pytest tests/e2e/`. `tests/integration/test_sandbox_docker.py` (ADR-038 containment)
additionally requires the `ai-etl-sandbox:latest` image from `make sandbox-image`, which
CI never builds.

Net effect on every PR: `test_alembic_migration` (3), `test_audit_persistence` (5),
`test_tenant_isolation_rls` (4), `test_sandbox_docker` (8), `test_sandbox_vercel` (7) —
~27 integration tests skip silently and the job goes green.

They *do* pass on the maintainer's machine — this session's `make check` log lines
1245-1248 show all 4 RLS tests PASSED locally. A laptop is not a gate; a regression in
`tenant_scope()` or in migration `0021`'s policies would merge green.

Documented as known? **No.** The opposite: the docs cite the test file as proof.

**6. GitHub is an undisclosed sub-processor handling a full production dump with PII.**

`.github/workflows/db-backup.yml` runs `pg_dump --schema=public` of the production
Supabase database — `users`, `runs`, `analysis_runs`, `saved_pipelines`, `tenant_secrets`
— onto a **GitHub-hosted Actions runner**, writes it to the runner's filesystem
(`--file=backup.dump`), then uploads it to S3. `docs/compliance/sub-processors.md`
(dated 2026-09-06, one day before this workflow's first clean run on 2026-09-07) lists
Clerk, Supabase, Railway, Vercel, AWS S3, OpenAI, Anthropic, Google — **GitHub is absent**.

Why it matters: this is a literal LGPD Art. 39 / GDPR Art. 28 sub-processor-disclosure
question, and the register is the document written specifically to answer it. The
register's own review cadence says *"reviewed whenever a new vendor is integrated"* — a
new data-processing vendor was integrated the next day and the register wasn't touched.

Documented as known? **No.**

**7. Right-to-erasure does not reach the backups the same sprint created.**

`services/tenant_deletion_service.py` deletes DB rows plus the tenant's own S3 key prefix.
`grep -rn "_system\|db-backups" src/ai_etl/` → **no matches**. The daily dumps land at
`s3://…/_system/db-backups/production/${DATE}.dump` with no lifecycle or expiry rule
(`grep -rn "lifecycle" infra/` finds only an ECR image policy). ADR-025 never mentions
backups; neither does `docs/compliance/lgpd-gdpr-data-processing.md`. So a tenant who
exercises `DELETE /tenant` still has their personal data in every prior daily dump,
indefinitely.

The 2026-09-07 backup work closed one compliance gap (row 4 of the risk register, "Closed")
and silently opened another. That the deletion service is otherwise careful — it logs
per-table row counts to feed the erasure log — makes the omission more visible, not less.

Documented as known? **No.**

---

### MEDIUM

**8. CodeQL is still disabled on a rationale that expired 9 days ago.**

`.github/workflows/codeql.yml`'s header comment, `SECURITY.md:163-166`, and ADR-032
Decision 3 all justify disabling GitHub's native SAST because *"code scanning on this
**private** repository requires GitHub Advanced Security, which this account's plan
doesn't include."* But:
```
$ gh repo view … --json visibility,isPrivate
{"isPrivate": false, "visibility": "PUBLIC"}
```
The repo has been public since 2026-08-30 (own memory/CURRENT_STATE record). Code
scanning is free on public repositories. A free, native, dual-language security control
is off, and three separate documents still assert the expired reason as current fact.
The workflow was even designed to be re-enabled by uncommenting three triggers.

Documented as known? **No** — worse, documented as a still-blocking constraint.

**9. `CHANGELOG.md` claims branch protection the repository does not have.**

`CHANGELOG.md:52-53`: *"branch protection now requires 1 approving review on `main`,
`CODEOWNERS`, …"*. Live config:
```
$ gh api repos/brunoribeirol/ai-etl/branches/main/protection
required_approving_review_count: 0
require_code_owner_reviews:      false
enforce_admins:                  false
```
`.github/CODEOWNERS` exists (covering `core/sandbox.py`, `audit/`, `.github/workflows/`,
`pyproject.toml`) but is decorative with `require_code_owner_reviews:false`, and the sole
maintainer — an admin — can push directly to `main` with `enforce_admins:false`, despite
`CLAUDE.md`'s "never commit directly to `main`" non-negotiable.

This matters beyond hygiene: SOC2 CC5's accepted position and `risk-register.md` row 7
both rest on *"branch protection + required PR + CI gate enforced (`CLAUDE.md`)"* being
true as configuration. In practice it is true as discipline —
`git log --first-parent -25` shows every recent commit arriving via a numbered PR — but
discipline is not what the compliance docs claim.

Documented as known? **No — the docs claim the stronger version.**

**10. Semgrep is documented as a blocking check; it is not a required check.**

ADR-032 Decision 3 (*"before this became a blocking check"*) and `SECURITY.md:166-171`
present Semgrep as the enforced CodeQL substitute. The only required contexts on `main`
are the two `Python 3.11/3.12 — Lint, Format, Type, Test, Security` jobs. **Semgrep,
the `e2e` job, Frontend CI, Docker and Terraform are all non-blocking** — a PR can be
merged with any of them red. Combined with finding 8, the repo's entire SAST layer is
currently advisory.

Documented as known? **No.**

**11. ADR-039 was never amended after its mitigation was ruled out by a billing decision.**

`SECURITY.md:70-73` points a reader at ADR-039 as *"the stronger mitigation, still not the
production default."* ADR-039's own text still argues the case forward: line 46 —
*"the Hobby (free) plan's 5 included Active-CPU-hours/month likely covers this project's
current pre-launch traffic … at zero cost"*; line 222 — *"very likely sufficient"*; and
Follow-up item 2 reads *"Flip `AI_ETL_SANDBOX_BACKEND=vercel` in Railway's production
environment once (1) is done."* `grep -iE "403|forbidden|declined|billing" ADR-039` returns
**nothing about the actual outcome.**

The real outcome — `HTTP 403: You don't have permission to create the vercel sandbox
(code=forbidden)` on the Hobby plan, owner declined the Pro upgrade — exists only in
`docs/CURRENT_STATE.md:537-561` and `:641`. A reader doing ADR-first due diligence (the
normal path) concludes the primary sandbox mitigation is one env var away. It is actually
blocked behind a purchase the owner declined on 2026-08-31.

Documented as known? **Yes in `CURRENT_STATE.md`, no in the ADR a reader is sent to.**

**12. User-facing errors are still hardcoded Portuguese despite ADR-036 i18n.**

PR #221 (2026-09-08) fixed this exact bug class for progress messages. Still live:
- `services/execution_queue.py:212-214` — *"Limite de 10 execuções por 60 minutos atingido
  para esta conta. Tente novamente mais tarde."* → surfaced verbatim as the HTTP 429
  `detail` (`api/routers/runs.py:359-360`).
- `services/execution_queue.py:314-315` — budget-cap message → HTTP 402
  (`api/routers/runs.py:361-366`).
- `services/health_alerts.py:46,51,54` (alert emails), `services/digest.py:59`,
  `agents/analysis/advisor.py:160,195` (strings injected into the Advisor's own output),
  `services/auth_service.py:122,136,152`.

An `en-US` tenant who hits the rate limit or the budget cap gets Portuguese.

Documented as known? **No** — the 2026-09-08 walkthrough entry reads as if this class
was closed.

**13. 7 of 47 ADRs still declare `Proposed` / "not merged" / "not applied to production"
for features that are live.**

From the status sweep: ADR-014 (`Proposed`; superseded in practice by ADR-031, which
references it as prior work), ADR-016, ADR-017, ADR-018, ADR-020, and notably:
- ADR-019 — *"Proposed (code complete, migration verified locally, **not applied to
  production**)"* while `alembic/versions/0009_tenant_budget_cap.py` is merged and
  `api/routers/budget.py` + budget enforcement are live.
- ADR-022 — *"Proposed (… migration `0011` … **not applied to production**)"* while
  `0011_tenant_secrets.py`, `services/secrets_service.py`, `api/routers/secrets.py` and
  `frontend/src/app/(app)/secrets/page.tsx` all ship.

For an examining board or an acquirer reading `docs/adr/` as *the* decision log, ~15% of
records misstate their own state.

Documented as known? **No.**

**14. `CHANGELOG.md` frozen at v1.0.0 (2026-08-22) with an empty `[Unreleased]`.**

`git log -1 -- CHANGELOG.md` → 2026-08-22. Since then: ~130 PRs (#95 → #225), 16 new ADRs
(032-047), and essentially every security control the project now advertises — seccomp
(ADR-046), RLS defense-in-depth (ADR-040), backups (ADR-042), admin mediated write access
(ADR-047). `git tag` → exactly one tag, `v1.0.0`. `pyproject.toml:3` still says
`version = "0.1.0"`. The v1.0.0 entry's own stated purpose was to *"establish real release
hygiene going forward."*

Documented as known? **No.**

---

### LOW

**15. `SECURITY.md`'s and ADR-003's lead paragraphs describe a codebase that no longer
exists.** `SECURITY.md:24-41` opens with *"`exec()` is used at three call sites … none of
the three sites enforce an actual execution timeout"*, then corrects itself in three
successive "Update" bullets. Verified reality: `grep -rn "exec("` finds exactly one real
pair, both in `core/sandbox.py:209` and `:245`, both `# nosec`/`# nosemgrep`-annotated,
with an enforced timeout. Likewise ADR-003's *"Follow-up (open as of 2026-08)"* (unify the
three whitelists) was resolved by ADR-007 but is never marked resolved in ADR-003. Both
documents are accurate by the end; a skimmer reads the top.

**16. ADR-042's own topology table is stale inside an otherwise fully-verified ADR.**
Line ~35: *"Celery beat (scheduler) | **Not yet deployed** | N/A — scheduled pipelines
don't fire in production today."* `CURRENT_STATE.md`'s 2026-09-08 entry confirms
celery-beat is deployed and its `check-scheduled-pipelines` task fires every 60s, watched
live in Railway logs.

**17. `infra/aws/terraform/` (13 `.tf` files + a dedicated CI workflow) is never-applied
infrastructure.** ADR-015 is completely upfront about this — its title is literally
*"Terraform, not applied"* and it says the proof is *"torn down after review, never
applied"*. Honest, and I count the restraint as a positive. But a tree-browsing DD reader
sees an AWS deployment path that doesn't exist; one banner line in
`infra/aws/terraform/README.md` would close it.

**18. `db-backup.yml` has no failure alerting and no backup retention policy.** The
workflow verifies the uploaded object is >1024 bytes and stops there — no `if: failure()`
notification, and no S3 lifecycle rule on `_system/db-backups/`. ADR-042's verified
"RPO ≤24h" holds only while the schedule keeps succeeding. Separately: GitHub disables
`schedule` triggers after 60 days of repository inactivity — a live scenario for a
capstone that goes dormant after December, at which point the RPO silently returns to
infinite with no signal.

---

## Verified as good

These are real, and the verdict depends on them as much as on the findings.

- **`make check` is genuinely green.** From the session log: `EXIT=0`,
  `1199 passed, 14 skipped`, coverage **95.11%** against an 80% gate, bandit clean,
  `pip-audit` → *"No known vulnerabilities found."* The 14 skips are all legitimately
  environment-gated (Vercel Sandbox creds, MySQL/Mongo services, a Linux-only seccomp test).
- **No secrets, no personal credentials, no personal cloud identifiers in committed code.**
  Greps for the owner's email, `pk_live`/`sk_`, 12-digit AWS account ids, `s3://` literals,
  Supabase/Railway hostnames across `src/`, `frontend/src/`, `.github/`, `infra/` returned
  exactly one benign `git clone` URL in `release.yml`. `.env` is gitignored,
  `.env.example` and `frontend/.env.example` are both tracked so the quick-start works.
- **No dead subsystems.** Streamlit is fully retired — `app.py` gone, the `app` extra
  removed from `pyproject.toml`, and a regression test pins it shut
  (`tests/unit/test_pipeline_service.py:1520::test_services_package_does_not_import_streamlit`).
  The ADR-jargon leak into the production UI that a prior audit found is fixed —
  `grep "ADR-" frontend/messages/*.json` is empty. Every connector in `sources/` and
  `destinations/` is reachable from the frontend's own type unions.
- **`exec()` discipline holds.** Exactly one call site pair, both in `core/sandbox.py`,
  exactly as `CLAUDE.md`'s non-negotiable rule requires.
- **ADR-040 is real engineering, not paper defense-in-depth.** Two roles, per-transaction
  GUC binding via `set_config(..., true)` with a documented reason for rejecting
  `SET LOCAL` (it can't take a bound parameter — would have violated the project's own
  no-f-string-SQL rule), real `USING`/`WITH CHECK` policies in migration `0021`, and a
  pooled-connection-reuse leak test that actually passes. It was also built *ahead of*
  ADR-032 Decision 1's own stated trigger, which is the right instinct.
- **ADR-042's backup work is the strongest artifact in the repository.** A real finding
  (Supabase Free Plan = zero backups, contradicting the ADR's own prior assumption), an
  owner decision among three costed options, six distinct real failures each documented so
  the next person doesn't rediscover them, a real 40,418-byte dump, and a real `pg_restore`
  rehearsal at 1.674s with row counts confirmed matching production. This is what
  "verified" should look like everywhere in the repo.
- **`docs/compliance/` is auditor-shaped and honest.** The sub-processor register states
  *"this project has not independently audited any of them — that's the nature of relying
  on a sub-processor"*; the risk register carries severity/likelihood/owner/review-date and
  concrete revisit triggers; the incident response plan names a single responder rather
  than inventing an on-call rotation. The self-assessment labels itself
  *"not a certification, not an external audit"* in its first line.
- **`SECURITY.md`'s Supabase RLS section is unusually good writing** — it documents a real,
  remotely exploitable hole found in production on 2026-08-21, and warns the reader
  explicitly not to generalize the `rolbypassrls` no-op fix to any other database.
- **Restraint where restraint was right.** ADR-015 and ADR-043 both ship zero code and say
  so in their first paragraph; ADR-029 chooses manual-only prompt regression and defends
  the LLM-cost trade-off. ADR-032 Decision 1's *"Option C — enable RLS with policies but
  keep `rolbypassrls=true`"* is rejected with the line *"a posture that looks safer than it
  is is worse than an honestly documented gap."* That sentence is the project's best
  instinct, and most of the codebase lives up to it.
- **The two ADR-032 leads I was asked to check are honestly closed, not stale.** Decision 1
  is marked superseded by ADR-040 in ADR-032's own header *and* at the top of the Decision;
  Decision 4 is marked superseded by ADR-038. `audit/connection.py:1-25` describes the
  two-role split accurately and `get_tenant_engine()` fails closed
  (`EnvironmentError`) rather than silently falling back to the bypass role.
- **Change practice is clean even where enforcement isn't.** All 25 most recent
  first-parent commits on `main` arrived through numbered PRs; Dependabot is active and its
  PRs are being merged (#214-#219 on 2026-09-08).

---

## Unverified / could not check

- Whether `APP_DATABASE_URL_TENANT` is actually set in Railway production. I deliberately
  did not read production env vars. The code fails closed
  (`audit/connection.py:62-64` raises), and the app serves traffic, so it is almost
  certainly set — but that is inference, not verification.
- Whether the production Postgres role *literally* still has `rolbypassrls = true`. That
  needs `SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user` against
  production; I used no DB connection. Code, migrations and docs all consistently assume
  yes.
- Clerk's exact current development-instance quotas. I verified the instance **type** from
  the live publishable key; I did not re-read Clerk's published limits this session, so I
  have not put a specific user-cap number in the finding.
- Whether `db-backup.yml` has actually run successfully on any day since the 2026-09-07
  first clean run. Would need the Actions run history; not checked.
- Whether Semgrep currently passes. It is not a required check and I did not run it.
- Frontend dependency risk (`npm audit`, bundle analysis). Outside `make check`'s scope and
  not run here — `frontend-ci.yml` only does lint + build, and is not a required check.
- ADR-047's `POST /admin/tenants/{id}/pipelines/{id}/retry` and PR #223's
  `POST /llm/test-connectivity` wiring — both flagged by `CURRENT_STATE.md` itself as
  built but never live-verified. I did not verify them either (no browser, no prod creds).

---

## Persona verdict

**Finished as a capstone artifact; not finished as a business.** As a TCC and portfolio
piece this is over-delivered and defensible: 47 ADRs, 1199 passing tests at 95% coverage,
a real multi-agent architecture, and a documentation culture that repeatedly caught its own
false assumptions in writing — the Supabase-has-no-backups finding and the two
"fix that looked right on macOS / passed a weak bar and was still wrong" corrections are
exactly the kind of intellectual honesty an examining board rewards.

As a commercial asset it is not close, and the gap is not code volume — it is that the
product **cannot take money** (zero billing code anywhere in `src/` or `frontend/src/`),
**cannot legally be used by anyone else** (no `LICENSE` in a public repo whose README
promises MIT), and **authenticates its public users on a development-mode identity
provider** while giving every anonymous signup an uncapped spend allowance against the
founder's own API keys. Those four are the whole distance between "impressive system" and
"company," and none of them is a hard engineering problem.

The documentation-vs-reality gap is narrower than in most projects at this stage, but it is
concentrated in exactly the places due diligence looks first: the ADR a reader is pointed
to for the sandbox mitigation never learned that mitigation was cancelled; three documents
still justify a disabled SAST because the repo is private when it has been public for nine
days; the CHANGELOG promises a review requirement the branch-protection API says is zero;
and the test file cited as proof of tenant isolation never runs in CI. Every one of those
is a fifteen-minute fix and each one, left standing, costs more trust than the underlying
issue does.
