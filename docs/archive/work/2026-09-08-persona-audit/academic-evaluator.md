# Academic Evaluator — TCC Narrative Honesty

**Date:** 2026-09-09. Repo-only (Obsidian vault out of reach). Mode: reads.

## Findings

### AE-1 — A real case-study evaluation exists, but it's partial and dated (MEDIUM)
`case_study/results/tabela-resultados.md` + `runs_log.csv`: **15 real runs** (5 per scenario ×
3 scenarios), gpt-4o-mini, dated **2026-06-23**, with per-run status/time/quality/rows/
LLM-attempts and real run_ids. This is a genuine DSR artifact evaluation — more than a bare
harness. Also present: `sprint8/comparison_runs.csv` + `stability_summary.json` (model
comparison), `sprint28/baseline_metrics.json` (committed baseline), `sprint34` load test.
Caveats an examiner will raise:
- **Every one of the 15 runs is `quality = warning`** (none "passed"/"ok"). Not necessarily
  wrong, but the case study's own headline result is "warning on every run" — needs a defense.
- **Coverage is 3 scenarios; README advertises 4** (CSV/PG/REST/**PDF-DOCX**). Scenario 4
  (document) and the newer scenario 5 (sqlite+rest+auth, in e2e) have **no committed
  case-study results**. The `scenario2/` and `scenario3/` result dirs are `.gitkeep`-only —
  only the aggregate table + csv are committed, not per-run artifacts.
- **Dated 2026-06-23, single model.** The product now offers 4 models incl. Claude; there is
  no committed final multi-model case-study comparison for the case-study scenarios.

### AE-2 — Not reproducible from the repo alone (MEDIUM)
`case_study/data/` is gitignored, so a board member cloning the repo cannot reproduce a run
from the README instructions (the datasets are absent). The engineering is reproducible
(tests, migrations); the *evaluation* is not.

### AE-3 — No automated evidence of agent-output quality (MEDIUM) [see QA-1]
The thesis contribution is "agents generate auditable code end-to-end" and turn data into
recommendations. e2e mocks the LLM, and prompt regression is manual-only (ADR-029). So the
central artifact's *quality* is evidenced only by the (real, dated) 15-run table and manual
checks — not by anything a board can re-run. This audit's own live run (llm-prompt-engineer.md)
found the Advisor's headline answer correct but its secondary figures wrong — the kind of
thing an examiner probing "how do you know the recommendations are right?" will surface.

### AE-4 — Claimed-vs-actual sample: engineering claims hold up (PASS, with doc caveats)
Verified live/in-code: multi-provider LLM is real (OpenAI + Anthropic both wired and
live-tested this session via `/llm/test-connectivity`); the Agentic BI layer really produces
prioritized recommendations (live run); tenant budget cap, drift, RLS all have real code +
migrations. The engineering narrative is honest and over-delivered. The gaps are in the ADR
*status* hygiene (7 ADRs still "Proposed" for shipped features — see tech-lead.md 4c) and the
sandbox/SECURITY doc drift (tech-lead 4a/4b) — documentation understating the code.

### AE-5 — 47 ADRs are genuine decision records (PASS)
Sampled ADR-015/029/032/040/042: each carries context, alternatives-considered, and explicit
revisit triggers; several document their own disproven assumptions (ADR-042's Supabase-Free =
no-backups finding, the six failed backup attempts). This is strong DSR rigor and the single
most defensible part of the repo for a board.

## Verdict
As a TCC artifact the engineering is over-delivered and the decision record is exemplary. The
*evaluation chapter* is the soft spot: partial scenario coverage, an all-`warning` result set,
single-model/dated numbers, non-reproducible data, and no automated agent-quality evidence. A
board will accept the system; it will press on the evaluation's completeness and honesty.
