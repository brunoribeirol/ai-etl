# Skill: persona-audit

Multi-persona technical + product audit, formalized from the ad hoc protocol used on
2026-08-24 (`docs/work/2026-08-24-full-technical-product-audit.md`) — that session ran 12
personas as one-off prompts inside a conversation with no reusable artifact. This skill makes
it repeatable.

**When to run this:** before a major release, before a TCC defense, or periodically (e.g. via
`/loop` on a monthly cadence) to catch drift between what's documented and what's actually
shipped.

## Protocol

### Step 1 — Scope

Confirm with the user: full audit (all personas below) or a subset? Full audit is expensive
(12 independent investigations) — default to asking which personas matter most for the current
concern (e.g. pre-defense → weight Academic Evaluator + CTO/Founder; pre-launch → weight Red
Team + SRE + Product/PO).

### Step 2 — Run each persona as an independent investigation

Each persona should **execute against the real system**, not just read code — the 2026-08-24
audit's highest-value findings (the SQL-injection RCE, the Word-table data loss, the
Advisor ignoring its own sanity-check warning) were all found by *running* the real pipeline
with real inputs, not by static reading. Prefer spawning each persona as a separate agent
(general-purpose, isolation: worktree if it needs to run code) so investigations don't
cross-contaminate each other's assumptions.

| Persona | Focus | Typical method |
|---|---|---|
| CTO/Founder | Due-diligence red flags, gap between docs and reality | Cross-reference `docs/adr/`, `SECURITY.md`, vault `artefact/` against actual code |
| Tech Lead | Code quality, architectural drift, tech debt | `make check`, dependency graph, ADR "Follow-up" sections left unresolved |
| Data Engineer | Real data edge cases | Feed genuinely messy files (multi-sheet Excel, `.docx` with tables, mixed encodings) through the real pipeline |
| LLM/Prompt Engineer | Prompt quality, hallucination risk, agent output correctness | Run real LLM calls, inspect narratives/code against ground truth |
| QA | Test suite honesty | Run `tests/integration/` and `tests/e2e/` for real (not just check they exist) against real Postgres/Redis |
| Red Team | Exploitable vulnerabilities | Attempt real attacks (SQL injection payloads, sandbox escape) against a throwaway instance |
| Blue Team | Defensive gaps | Audit logging, secret redaction, RBAC boundary testing |
| SRE | Operational readiness | Real deploy health checks, alerting, what happens when a dependency is down |
| Product/PO | Backend/frontend parity | Cross-reference every OpenAPI route against every frontend fetch call — flag backend-complete/zero-UI features |
| Non-technical user | Real usability | Use the actual UI live, note jargon leaks, confusing flows |
| Accessibility | a11y compliance | aria attributes, keyboard nav, screen-reader labels on the real rendered UI |
| Academic evaluator | TCC narrative honesty | Cross-reference `writing/drafts/` (vault) claims against `docs/CURRENT_STATE.md`'s actual shipped state |

### Step 3 — Consolidate

One document, `docs/work/YYYY-MM-DD-<slug>-audit.md`, with:
- Per-persona findings (link to each persona's own investigation notes if they were separate
  agent transcripts).
- A single prioritized list across all personas, deduplicated (the 2026-08-24 audit found the
  same gap independently from 2 personas more than once — that's a *stronger* signal, not
  redundant noise; note when this happens).
- Explicit "not fixed yet" vs "fixed same-session" status per finding.

### Step 4 — Action plan (separate step, not automatic)

Do NOT start fixing things as part of the audit itself unless explicitly asked to — the
2026-08-24 precedent was audit-only by deliberate request, with the fix pass done as its own
follow-up session using the `work-plan` skill. Keep "found the problem" and "decided how to fix
it" as distinct, separately-approved steps.

## Related

- `docs/work/2026-08-24-full-technical-product-audit.md` — the original, unformalized run of
  this protocol.
- `work-plan` skill — use for the follow-up fix-execution plan once findings are prioritized.
