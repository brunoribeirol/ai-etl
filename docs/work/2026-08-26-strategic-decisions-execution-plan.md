# 2026-08-26 — Execution plan: post-audit strategic decisions

**Status:** All implementation work done. All 5 branches pushed, reviewed, and ready. Nothing left to build before 2026-09-01 — only waiting on the CI budget reset.
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
5. **(added mid-session)** GitHub Actions budget hit $17.34/$18, later
   re-checked at $17.56/$18 — all PR opening held until the 2026-09-01
   reset, explicitly chosen over risking even one more PR with $0.44 left.
   See `~/.claude/projects/.../memory/project_ci_budget_constraint.md` for
   the confirmed playbook.

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
local branches, 51 already-merged **remote** branches (this repo never had
"auto-delete head branches" on — going back to `feat/sprint3-*`), 13 orphaned
`.claude/worktrees/` directories, and `docs/session-consolidation`
(confirmed-obsolete branch, 2026-08-21 CURRENT_STATE notes superseded by
later entries).

## Wave 2 — status: DONE, held pending CI budget

| Item | Branch | State |
|---|---|---|
| Per-pipeline notification config UI | `feat/notification-config-ui` | Pushed, no PR yet. Built as its own `PUT /pipelines/{id}/notification-config` endpoint pair (not a `PATCH` field — clearing needs `channel=None,target=None` distinguishable from "omitted"), wired into `pipelines-manager.tsx` next to `ModelPicker`. |
| LGPD export/retention UI | `feat/data-export-retention-ui` | Pushed, no PR yet. New `/data-export` page — first consumer of `GET/PATCH /tenant/retention`, `GET /tenant/export`, and `DELETE /tenant` (ADR-025, ADR-035). **Both scope questions from the first version resolved** (owner's decision, 2026-08-27, "follow LGPD fully"): `DELETE /tenant` now wired in, editor-gated, requires typing the literal `DELETE` to enable the button (mirrors the backend's own `confirm: "DELETE"` contract, ADR-025 Decision 4, rather than a weaker client-side gate); `PATCH /tenant/retention` now has a full save/clear form, same pattern as `budget-manager.tsx`'s cap form. |

Both branch off `refactor/english-only-repo-wide` correctly (not stale
`main`) — built in `/tmp` clean clones due to the git incident below, so
this was verified explicitly rather than assumed.

## A real local-environment incident this session — resolved

Mid-Wave-2, the local checkout under `~/Documents/ai-etl` (iCloud-synced)
started crashing with `SIGBUS` (`error: reset died of signal 10` /
`pack-objects died of signal 10`) on `git checkout`, `git reset`, worktree
creation, and eventually `git push` — a new variant of this project's
recurring iCloud/git friction, not a hang this time, an explicit crash.
Diagnosed and fixed via this project's own established recovery (confirm
everything is pushed, `rm -rf` the local repo, fresh `git clone` from
GitHub) — **not** by moving the project off iCloud, which was floated but
turned out unnecessary. Zero data loss: all branches existed on GitHub
throughout. Full writeup: vault `bugs-solved/mypy-pytest-hang-agent-sandbox.md`
and `bugs-solved/git-object-store-corruption-parallel-worktrees.md`,
both updated 2026-08-26.

## Next steps, in order

**Now, CI-budget-blocked (until 2026-09-01) — nothing left to build:**

All implementation is done. `frontend-design-review` and `/sr-quality-check`
were both run (2026-08-27) over all 3 pending frontend branches
(`refactor/english-only-repo-wide`, `feat/notification-config-ui`,
`feat/data-export-retention-ui`) — zero findings: no Portuguese identifiers
in any diff, i18n key parity confirmed on both `feat/notification-config-ui`
and `feat/data-export-retention-ui`, `npm run lint`/`npm run build` clean on
all 3 re-verified from a fresh clone. Both Wave 2 scope questions are
resolved (see the table above) — nothing left in this plan's own scope to
decide or build before the reset. The only remaining action is waiting.

**2026-09-01, once the Actions budget resets:**
1. Open PRs in dependency order: `refactor/english-only-repo-wide` first
   (foundation for everything else) → `feat/docker-sandbox-migration`
   (isolated, backend-only, already reviewed clean) → `feat/notification-config-ui`
   → `feat/data-export-retention-ui` → `docs/current-state-2026-08-26` last
   (documents the state this whole batch produces, so merge it after
   everything else is actually in).
2. In the **first** PR of that batch, also add
   `if: github.event.pull_request.draft == false` to every workflow in
   `.github/workflows/` — pays for that PR's CI anyway, saves money on every
   future PR held in draft.
3. Watch CI, merge each once green, in the same order.
4. Delete merged branches + worktrees afterward (same cleanup pattern as
   this session).

**After that batch lands — real remaining work, not started, no branch yet:**
5. Sandbox Docker migration's production rollout is explicitly **deferred**,
   not done: Railway doesn't support Docker-in-Docker (non-privileged
   containers — confirmed against Railway's own docs, cited in ADR-038).
   Needs a separate execution service (its own Railway service, or another
   Docker-capable host, called over an internal API) — this is a real
   architecture decision for Bruno to make before any further sandbox work,
   not something to default into.
6. Remaining backend-ready/zero-UI features not yet in this plan: none
   currently known beyond Wave 2 — re-check `docs/CURRENT_STATE.md`'s
   "not done" list before assuming this is exhaustive.
7. `docs/adr/ADR-032-security-posture-admin-role-sast.md` Decision 4 already
   carries a superseded-by-ADR-038 note — no further action needed there.
8. The project's new `metrics-analyst` agent (created 2026-08-26) hasn't
   been used yet — no urgency, but a real gap if the TCC write-up needs a
   fresh model-comparison/case-study report at some point.

## Acceptance criteria

- W2a/W2b: `make check` clean, `npm run lint`/`npm run build` clean, new
  frontend surface reachable from nav, English-only naming from the start
  (no new Portuguese identifiers), role-gating follows the established
  cosmetic-client-check + real-server-check pattern.
- All 4 pending branches: local CI-equivalent green before any push (per
  the CI-budget playbook), real CI green before merge once opened.

## Risks

- iCloud sync can still cause `SIGBUS` on `.git` operations under this
  project's working directory (happened once this session, see the
  incident section above) — if it recurs, the fix is: confirm everything is
  pushed (`git status --short -uall` + `git ls-remote`), then `rm -rf` the
  local repo and `git clone` fresh from GitHub. Don't spend time on lighter
  workarounds first (repack, force-reading packs, force-materializing the
  tree) — all were tried this session and none fixed it; re-clone is what
  actually worked, both this time and the 3 prior "pack too short"
  occurrences. Full detail in the vault's `bugs-solved/mypy-pytest-hang-agent-sandbox.md`
  and `bugs-solved/git-object-store-corruption-parallel-worktrees.md`.
- Wave 2 branching off `refactor/english-only-repo-wide` instead of `main`
  means its eventual PR's diff will look larger against `main` (includes
  the rename) unless `refactor/english-only-repo-wide` merges first — this
  is intentional per the dependency-order plan above, not an oversight.
