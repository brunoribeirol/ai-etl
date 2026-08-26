# Skill: frontend-design-review

Project-specific frontend conventions for AI-ETL's Next.js app, accumulated across sprints —
check new frontend work against these before it ships, and use this as the checklist a
`/code-review` or manual review should apply on top of generic React/Next.js best practices.

## Non-negotiable: naming is English, always

**Every route path, component name, file name, variable, and prop must be in English** — no
exceptions, regardless of what surrounding code already does. Portuguese (or any other
non-English language) is allowed **only** as user-visible string content inside
`frontend/messages/{en-US,pt-BR}.json`, selected at runtime by `locale-toggle.tsx`.

This project has a real, pre-existing violation of this rule: routes like `/historico`,
`/aprovacoes`, `/resumo`, `/comecar`, `/executar`, `/segredos`, `/orcamento` are named in
Portuguese. **Do not extend this pattern to any new route** — if you need a route for a feature
whose obvious name would be Portuguese by analogy to its neighbors, use the English name anyway
and flag the inconsistency instead of matching it. A batch rename of the existing routes to
English (`/history`, `/approvals`, `/summary`, `/get-started`, `/run`, `/secrets`, `/budget`)
is tracked as pending technical debt — see `docs/CURRENT_STATE.md` for status before assuming
it's done.

## Established UI patterns (reuse, don't reinvent)

- **Destructive/consequential action confirm:** inline 2-click confirm (click once to reveal a
  "confirm?" state, click again to commit) — no new shadcn dependency needed. First used in
  `approval-queue.tsx` (PR #145) for approve/reject; reused for budget-cap clearing and secret
  deletion. Reach for this before adding a modal/dialog library for a simple confirm.
- **Role-gating:** cosmetic client-side check (hide/disable based on `/config`'s `role` field)
  is never sufficient alone — the real enforcement is always the server-side dependency
  (`require_role(...)`/`require_admin` in `api/deps.py`). Every gated UI element needs both:
  the client-side hide for UX, and confirmation that the route it calls actually enforces the
  role server-side (don't assume — check the router file).
- **Accessibility baseline** (Wave 4, 2026-08-25): `aria-live`/`role="status"` on any stepper or
  async-updating UI; `aria-hidden` on purely decorative icons; numbers formatted via
  `Intl.NumberFormat`, never raw string interpolation of a float.
- **Plain-language over jargon:** no internal architecture-decision references (e.g. "ADR-016")
  should ever leak into user-facing copy — that's a real bug this project shipped once
  (PR #136) and fixed; don't reintroduce it.
- **i18n:** every user-facing string goes through `next-intl`/`messages/{en-US,pt-BR}.json` —
  never a hardcoded string in a component, in either language.

## Before shipping any new frontend PR

1. `npm run lint` and `npm run build` clean (see `docs/work/2026-08-25-*.md` for the
   iCloud-sync `node_modules` hang workaround if either command hangs).
2. Every new route name, component name, and file name reviewed against the English-only rule
   above.
3. Any destructive/consequential action has a confirm step matching the established pattern.
4. Any role-gated element's server-side enforcement confirmed, not assumed.
5. New strings added to both `en-US.json` and `pt-BR.json`, never hardcoded.
