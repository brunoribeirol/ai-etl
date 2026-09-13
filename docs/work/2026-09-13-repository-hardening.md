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
- Trivy gate green on `main` since #259.
- Wikis and Discussions disabled; Issues enabled.

## Remaining work

1. **AWS OIDC** — `.github/workflows/db-backup.yml` still authenticates with permanent AWS
   access keys stored as repository secrets. Migrate to GitHub OIDC + an AWS IAM role to
   remove long-lived credentials. See `docs/adr/ADR-042-rto-rpo-disaster-recovery.md`.
2. **GitHub Actions SHA pinning** — all actions are still pinned by tag (`@v7`, `@v0.36.0`, …).
   Pin every action to a full commit SHA **first**, then enable the repository option that
   requires full-length SHA references. Enabling that setting before pinning breaks every
   workflow.
