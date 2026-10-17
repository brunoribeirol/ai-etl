# Repository Hardening — 2026-09-13

## Scope

Repository governance and CI security gates. **No application code was changed.**
Only `.github/` configuration and the production `Dockerfile`.

This note records the rules a contributor (human or agent) will hit when opening a PR,
and the security decisions behind them — so nobody has to re-derive them from the GitHub UI.

## Changes

| PR | Change |
|---|---|
| #245 | Dependabot version updates expanded to 5 ecosystems: `uv`, `npm` (`/frontend`), `github-actions`, `docker`, `terraform` (`/infra/aws/terraform`) |
| #258 | `codeql.yml` skips analysis when code scanning is unavailable instead of failing |
| #259 | Trivy became an enforcing gate + Debian security updates applied in the `Dockerfile` |
| #265 | Dependabot minor/patch grouping per ecosystem |
| #266 / #267 | First grouped Dependabot PRs (frontend: 12 updates; Python: 20 updates) |
| #260-#264 | Five ungrouped Dependabot **security** PRs — closed all 11 open alerts (5 high, 6 moderate) |
| #271 | All 22 action references pinned to full commit SHAs |

## Security decisions

**Ruleset `default-branch-protection`** (active, targets the *default branch* by reference —
`main` is not hard-coded):

- restrict deletions, block force pushes, require linear history
- **require signed commits**
- PR required before merging; **required approvals = 0** (solo project — revisit when a second
  maintainer joins); conversation resolution required
- **squash is the only allowed merge method** (merge commits and rebase merge are disabled
  at the repository level too)
- required status checks, branch must be up to date before merging:
  - `Python 3.11 — Lint, Format, Type, Test, Security`
  - `Python 3.12 — Lint, Format, Type, Test, Security`
  - `E2E (Sprint 5) — full stack against real Postgres/Redis`
  - `Semgrep SAST`
- **code scanning is NOT a required check** — see CodeQL below. Never require a check that
  cannot run; it hard-blocks every PR.

**CodeQL — private-repo behavior.** The repository is private and code scanning is not
available under the current plan, so the analysis job is **conditionally skipped**. The
workflow stays fully configured with all its triggers and resumes automatically if the
repository becomes public, or can be enabled on a private repository later via a repository
variable, without editing the workflow again. Do not delete or comment out this workflow.

**Dependabot grouping strategy.**

- Security updates: **never grouped** — each fix arrives as its own reviewable PR.
- Version updates, minor + patch: **grouped per ecosystem**
  (`python-minor-patch`, `frontend-minor-patch`, `actions-minor-patch`), scoped with
  `applies-to: version-updates`.
- **Major updates: always separate.** Docker and Terraform are **not grouped at all** — a base
  image or cloud-provider bump gets its own review.
- Breaking majors currently open and deliberately **not** auto-merged: Plotly 7, Pandas 3,
  AWS provider 6, Python 3.14.

**Trivy gate on the production image** (`docker.yml`):

```yaml
severity: CRITICAL,HIGH
ignore-unfixed: true
exit-code: "1"
```

HIGH/CRITICAL **with an available upstream fix blocks CI**. Vulnerabilities with no upstream
fix are still reported but do not block — a gate that blocks on unfixable CVEs gets disabled.

**Debian security updates in the `Dockerfile`.** The first enforcing scan failed on CVEs
inherited from the base image, not from project code. Fixed by patching the runtime image at
build time:

```dockerfile
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*
```

**Python stays on 3.12.** The image is still `python:3.12-slim`; there was **no migration to
3.14** (the Dependabot PR proposing it fails this Docker job). The container still runs as a
non-root user.

## Current state

- Squash-only workflow, auto-merge enabled, head branches deleted after merge.
- `main` protected by the active ruleset above; signed commits are mandatory.
- GitHub Actions default workflow permissions are **read-only**, and Actions cannot create or
  approve pull requests.
- **SHA pinning is complete and enforced.** All 22 action references across the 9 workflows are
  pinned to full commit SHAs, and the repository setting `sha_pinning_required` is **`true`**,
  so an unpinned reference is now rejected rather than merely discouraged. Verified after the
  setting was enabled: zero unpinned `uses:` remain anywhere under `.github/` (no composite
  actions exist either), and a `Semgrep` run dispatched *after* enforcement went live completed
  successfully — the flag alone would not have proven the workflows still run.

  When changing a pin, keep the `# vX` comment: it is what makes the SHA reviewable, and
  Dependabot reads it to propose readable version bumps (the `actions-minor-patch` group
  updates the SHA and the comment together), so pinning does not freeze these actions.
- All 11 Dependabot alerts open at the start of the session are **closed** — the five ungrouped
  security PRs (#260-#264) merged.
- Trivy gate green on `main` since #259.
- Wikis and Discussions disabled; Issues enabled.

## Remaining work

**Deferred on 2026-09-13 to a later session — not forgotten, not blocked, just not now.**

1. **AWS OIDC** *(deferred)* — `.github/workflows/db-backup.yml` still authenticates with
   permanent AWS access keys stored as repository secrets. Migrate to GitHub OIDC + an AWS
   IAM role to remove long-lived credentials. See
   `docs/adr/ADR-042-rto-rpo-disaster-recovery.md`.

   Order matters: **the IAM role and the OIDC identity provider must exist in the AWS account
   before the workflow change merges.** Merging the workflow first leaves backups failing
   until the AWS side catches up, and a broken backup is worse than one using a static
   credential. Sequence: create the OIDC provider + role in AWS → change the workflow →
   verify one real backup run → only then delete the two access-key secrets from the repo.

2. **Four breaking-change major dependency PRs** *(deferred — open, deliberately unmerged)*:
   Plotly 7 (#269), Pandas 3 (#268), AWS provider 6 (#256), Python 3.14 (#246).

   These are not "merge when convenient". Each needs its own assessment of migration cost
   against benefit, and Pandas 3 in particular touches the Transformer/Quality/Analyst code
   paths that operate on DataFrames — the core of this project. Python 3.14 (#246) already
   fails the Docker job, so it is the clearest candidate to close rather than carry.

   Do not auto-merge any of them, and do not let a future Dependabot grouping change sweep
   them into a batch: the config keeps majors ungrouped precisely so each stays reviewable
   on its own.
