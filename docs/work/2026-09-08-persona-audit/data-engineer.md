# Data Engineer — Real Dirty-Data Behavior

**Date:** 2026-09-09. Mode: uploaded a genuinely messy multi-sheet Excel through the LIVE
app (`ai-etl.vercel.app`), real LLM run (gpt-4o-mini), inspected the Silver output.

## Test file
`vendas_sujas.xlsx` — 2 sheets: `Info` (decoy, sheet 0) + `Vendas2025` (real data, sheet 1).
The data sheet mixed: BR currency (`"R$ 1.234,56"`, `"89,90"`, `"250"`), mixed date formats
(`01/02/2026`, `2026-02-01`, `21/03/2026`, incl. DD-only-valid dates), a bad-type cell
(`quantidade="cem"`), a null product, an exact duplicate row, and inconsistent booleans
(`sim`/`não`/`SIM`). Business question attached ("Which product had the highest total
revenue?") to exercise the Agentic BI layer.

## Results (all confirmed against the live Silver output)

### DE-1 — Multi-sheet Excel: PASS (Wave-0 fix holds)
The sheet picker appeared ("this workbook has more than one sheet — choose which one to
use"); selecting `Vendas2025` produced that sheet's rows, not sheet 0's. Run completed.

### DE-2 — BR currency: PASS (Wave-1 fix holds)
Every price parsed to a real float: `R$ 1.234,56 → 1234.56`, `89,90 → 89.9`, `250 → 250`,
`R$ 3,50 → 3.5`, `R$ 12,00 → 12`, `R$ 45,00 → 45`, `R$ 2,00 → 2`. No nulls.

### DE-3 — Mixed date formats: PASS (Wave-1/2 fix holds)
All dates parsed under the tenant's pt-BR (day-first) convention with no visible NaT:
`01/02/2026 → 2026-02-01`, `21/03/2026 → 2026-03-21`, `15/12/2025 → 2025-12-15`, ISO
passthrough correct.

### DE-4 — Null-row handling: PASS (nice)
The null-product row was kept and flagged with an added `is_incomplete=true` column while
every complete row is `false`. Good, transparent handling.

### DE-5 — Bad-type cell silently coerced to 0 (MEDIUM)
`quantidade="cem"` (Lápis) became **0** in Silver, not NaN and not flagged. A non-numeric
quantity silently becoming 0 corrupts any quantity/units analysis (here it zeroed Lápis's
real 100 units, so revenue = 0 and Lápis vanished from the ranking). No `is_incomplete`-style
flag was raised for the bad *value* (only for the missing-product row). Worth a data-quality
warning when numeric coercion drops a value.

### DE-6 — Dedup is inconsistent (LOW)
The exact-duplicate Mochila row was removed (10 rows → 9), but the two identical Caderno rows
both remained. Dedup wasn't requested in the spec, so this isn't a hard bug, but the behavior
isn't consistent across identical-row groups within one run.

## Verdict
The dirty-data robustness the 2026-09-06 remediation shipped is **real and holds live** —
currency, mixed dates, multi-sheet, and incomplete-row flagging all worked on genuinely messy
input. The remaining edge is **silent numeric coercion** (`"cem" → 0`), which loses data
without surfacing it.
