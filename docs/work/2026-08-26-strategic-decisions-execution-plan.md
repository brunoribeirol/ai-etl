# 2026-08-26 — Execution plan: post-audit strategic decisions

**Status:** Wave 1 done, held pending CI budget reset (2026-09-01)
**Owner:** Bruno Ribeiro (decisions) + Claude (execution)

## Objective

Close the 3 remaining fronts left open after the 2026-08-25 audit-fix session
(`docs/CURRENT_STATE.md`, 2026-08-25 entry), plus 2 fronts that came up mid-session:
a real English-only naming violation, and a GitHub Actions budget constraint.

1. TCC text discrepancy — **done**. Vault `writing/drafts/draft-product-vision.md`:
   Agentic BI reframed from speculative/future to implemented/production.
2. UI for 4 backend-ready, zero-frontend features: tenant secrets, budget
   self-service, per-pipeline notification config, LGPD export/retention.
3. Sandbox: migrate `core/sandbox.py`'s restricted `exec()` to a real
   Docker-isolated sandbox (supersedes ADR-032 Decision 4).
4. **(added mid-session)** English-only naming — the frontend had Portuguese
   route names (`historico`, `aprovacoes`, `resumo`, `comecar`, `orcamento`,
   `segredos`) and `CLAUDE.md` itself was 100% Portuguese, both predating
   this session. Full rename done.
5. **(added mid-session)** GitHub Actions budget hit $17.34/$18 — all PR
   opening held until the 2026-09-01 reset. See
   `~/.claude/projects/.../memory/project_ci_budget_constraint.md` for the
   confirmed playbook.

Clerk dev mode and pricing: **explicitly deferred by the user**, no work
this round (Clerk — no domain purchase yet; pricing — after MVP validation).

## Non-goals

- Not touching Clerk config or auth flow.
- Not defining a pricing model.
- Not re-opening any of the 23 already-merged 2026-08-25 audit-fix PRs.

## Affected contracts

- `core/sandbox.py`'s `execute_in_sandbox()` signature does not change for
  callers (Transformer/Analyst/Science) — only the isolation mechanism
  underneath, behind an opt-in `backend`/`AI_ETL_SANDBOX_BACKEND` switch,
  default unchanged (`"process"`). Verified by the `architecture-reviewer`
  agent (see Wave 1 status below).
- New frontend components only for the 4 UI features; no existing
  route/schema changes needed (`GET/POST/DELETE /secrets`, `GET/PATCH
  /budget`, `PATCH /pipelines/{id}` notification fields, `GET
  /tenant/export`, `GET /tenant/retention` all already exist and are stable).
- All frontend route/component/i18n-key names are now English-only (see
  Wave 1 status).

## Wave 1 — status: DONE, held pending CI budget

| Item | Branch | State |
|---|---|---|
| Secrets management UI | `feat/secrets-ui` | **Merged** (#149) |
| Budget self-service UI | `feat/budget-self-service-ui` | **Merged** (#148) |
| English-only rename (routes/i18n/`CLAUDE.md`/skills+agents) | `refactor/english-only-repo-wide` | Pushed, no PR yet |
| Sandbox Docker migration (ADR-038) | `feat/docker-sandbox-migration` | Pushed, no PR yet — **reviewed by `architecture-reviewer`: PASS, 0 blocking findings**; 1 non-blocking hygiene note (missing `.dockerignore`) already fixed in the same branch |

Housekeeping done alongside: removed 1 stray duplicate file, gitignored an
ad hoc model-comparison results dir (#147, merged); deleted 34 merged/empty
local branches + 13 orphaned `.claude/worktrees/` directories +
`docs/session-consolidation` (confirmed-obsolete branch, 2026-08-21 CURRENT_STATE
notes superseded by later entries) + assorted scaffolding branches; recovered
from a real local `.git` corruption scare (iCloud-sync-related SIGBUS on
`git push`/`checkout` — not actual repo corruption, confirmed clean by
`git fsck --full`; worked around via a clean `/tmp` clone for the affected pushes,
same family of workaround as the documented `node_modules` iCloud bug).

## Wave 2 — not started

- W2a — Per-pipeline notification config UI (wire into
  `pipelines-manager.tsx`'s edit form, same pattern as `ModelPicker`;
  backend fields already exist, see `api/routers/pipelines.py`'s
  `notification_channel`/`notification_target`/`notification_active`, ADR-034).
- W2b — LGPD export/retention UI (tenant-facing settings page; backend
  routes `GET /tenant/export`, `GET /tenant/retention` already exist, ADR-035).

**Must branch off `refactor/english-only-repo-wide`, not `main`** — that
branch already renamed most of the frontend files/i18n keys Wave 2 will
touch; branching from stale `main` would repeat the merge-conflict class
already hit once between the secrets/budget UI PRs.

## Next steps, in order

**Now, still CI-budget-blocked (until 2026-09-01):**
1. Build Wave 2 (W2a, W2b) locally, branched off `refactor/english-only-repo-wide`.
   Push branches (free), hold `gh pr create`.
2. Run the `frontend-design-review` skill checklist over all pending frontend
   branches before they're considered done (not yet applied this session —
   only `architecture-reviewer` has actually been used so far).
3. Run `/sr-quality-check` formally over the full pending batch before
   opening anything.

**2026-09-01, once the Actions budget resets:**
4. Open PRs in dependency order: `refactor/english-only-repo-wide` first
   (foundation for everything else) → `feat/docker-sandbox-migration`
   (isolated, backend-only, already reviewed clean) → Wave 2 (W2a, W2b).
5. In the **first** PR of that batch, also add
   `if: github.event.pull_request.draft == false` to every workflow in
   `.github/workflows/` — pays for that PR's CI anyway, saves money on every
   future PR held in draft.
6. Watch CI, merge each once green, in the same order.
7. Delete merged branches + worktrees afterward (same cleanup pattern as
   this session).

**After that batch lands — real remaining work:**
8. Sandbox Docker migration's production rollout is explicitly **deferred**,
   not done: Railway doesn't support Docker-in-Docker (non-privileged
   containers — confirmed against Railway's own docs, cited in ADR-038).
   Needs a separate execution service (its own Railway service, or another
   Docker-capable host, called over an internal API) — this is a real
   architecture decision for Bruno to make before any further sandbox work,
   not something to default into.
9. Remaining backend-ready/zero-UI features not yet in this plan: none
   currently known beyond W2a/W2b — re-check `docs/CURRENT_STATE.md`'s
   "not done" list before assuming this is exhaustive.
10. `docs/adr/ADR-032-security-posture-admin-role-sast.md` Decision 4 already
    carries a superseded-by-ADR-038 note — no further action needed there.

## Acceptance criteria

- W2a/W2b: `make check` clean, `npm run lint`/`npm run build` clean, new
  frontend surface reachable from nav, English-only naming from the start
  (no new Portuguese identifiers), role-gating follows the established
  cosmetic-client-check + real-server-check pattern.
- All 4 pending branches: local CI-equivalent green before any push (per
  the CI-budget playbook), real CI green before merge once opened.

## Risks

- iCloud sync can still cause `SIGBUS`/hangs on `.git` operations
  (checkout/push) under this project's working directory — if it recurs,
  the fix is: `dd if=<pack> of=/dev/null` to force materialization, or a
  clean `/tmp` clone + fetch-from-local + push, not repeated retries of the
  same failing command. Worth a `bugs-solved/` note in the vault if it
  recurs a 4th time (already at 3 prior `.git/objects` corruption incidents
  per [[project_fase2_big_tech]] memory).
- Wave 2 branching off `refactor/english-only-repo-wide` instead of `main`
  means its eventual PR's diff will look larger against `main` (includes
  the rename) unless `refactor/english-only-repo-wide` merges first — this
  is intentional per the dependency-order plan above, not an oversight.
