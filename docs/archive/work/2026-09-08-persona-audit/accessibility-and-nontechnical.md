# Accessibility + Non-technical User

**Date:** 2026-09-09. Mode: live UI (accessibility tree + real use) on `ai-etl.vercel.app`,
every UI observation cross-checked against the actual frontend code before writing it down
(per the protocol's screenshot≠mechanism rule).

## Accessibility

### A11Y-1 — Theme-toggle aria-label is hardcoded Portuguese, ignores locale (MEDIUM)
Accessibility tree on the EN-locale `/app`: `button "Mudar para tema claro"` (Portuguese)
while the sibling locale toggle correctly reads `"Switch to Portuguese"` (English).
**Confirmed in code:** `frontend/src/components/theme-toggle.tsx:57`
`aria-label={isDark ? "Mudar para tema claro" : "Mudar para tema escuro"}` — hardcoded PT,
never routed through next-intl. An en-US screen-reader user hears Portuguese for the theme
control. Same i18n-leak class as PR #221 (progress messages) and CTO-12 (hardcoded PT errors).

### A11Y-2 — NOT a finding: the info button (checked, has a label)
The accessibility tree showed unnamed `button [ref_13/18/19]`. Grepping the code first (per
protocol) found the (i) info trigger DOES carry an accessible name:
`agents-info.tsx:46-47` `<Info aria-hidden="true" /> <span className="sr-only">{t("trigger")}</span>`.
The tree's "empty name" was an extraction artifact, not a real defect — not reported. (This is
exactly the trap that produced a phantom "nav-highlight" finding in a prior audit.)

### A11Y-3 — File upload is keyboard-accessible (PASS)
The Wave-2 `sr-only`+`peer-focus-visible` fix holds: the file input surfaces as
`button "File (CSV, Excel, JSON, PDF, DOCX)"` with a real accessible name, focusable.

## Non-technical user

### NT-1 — Results tabs use internal medallion/agent jargon (MEDIUM)
The run-detail tabs are labeled **Silver**, **Gold (2)**, **Advisor**, **Pipeline**, **Code**
(confirmed in `frontend/messages/en-US.json` and live). "Silver"/"Gold" are Databricks
medallion-architecture terms; a non-technical user (the stated audience — "turn data into
business recommendations") has no idea "Silver" = cleaned data or "Gold" = analysis. The
agents-info panel explains them, but the primary navigation exposes the vocabulary raw.
Consider user-facing labels (e.g. "Cleaned data", "Analysis", "Recommendations") with the
technical name secondary.

### NT-2 — Portuguese leaks on an English UI (HIGH — see consolidation P1)
Live, on an EN UI: the run progress line was "⚡ Executando pipeline Silver…" and the Advisor
narrative came back in Portuguese. For a non-technical English user this reads as a broken/
half-translated product. Root cause + mechanism in the consolidation (P1) and
llm-prompt-engineer.md (LP-5).

### NT-3 — The core flow is clear (PASS)
Upload → (sheet picker if multi-sheet) → optional manual spec → business question → pick model
(or default) → "Analyze data" → live stage tracker → tabbed results with a toast. The happy
path is genuinely legible to a non-expert; the friction is jargon (NT-1) and locale (NT-2), not
structure.

## Verdict
No blocking a11y defect found once mechanisms were verified in code (the careful sr-only work
mostly holds). The real cross-cutting issue is **locale/i18n leakage** (theme aria-label,
progress messages, narrative) plus **jargon in result labels** — both usability, both fixable.
