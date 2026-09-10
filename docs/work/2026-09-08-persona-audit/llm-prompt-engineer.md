# LLM / Prompt Engineer — Agent Output Correctness

**Date:** 2026-09-09. Mode: real LLM run (gpt-4o-mini) on the messy `vendas_sujas.xlsx`
via the live app; inspected the Advisor narrative + Gold against ground truth.

Question: "Which product had the highest total revenue?" Ground truth (revenue = Σ price×qty):
Caneta 24,691.20 (2 rows × qty 10 × 1234.56) ≫ Caderno 899 (2×5×89.9) > Mochila 500 (2×250) >
(null) 135 > Estojo 96 > Régua 40 > Lápis 0 (qty coerced from "cem").

## Findings

### LP-1 — Headline answer: CORRECT (strong positive)
Advisor: *"A Caneta gerou uma receita total de R$ 24,691.20"* — exact. It correctly picked
the highest-revenue product and computed price×quantity over both rows, on genuinely messy
input. Impressive end-to-end.

### LP-2 — Secondary figures internally inconsistent (MEDIUM)
The same narrative cites **Mochila R$ 250.00** (actual 500 — used price, ignored qty=2) and
**Cadernos R$ 179.80** (actual 899 — used 89.9 × 2 rows, ignored qty=5). So the revenue
methodology is applied inconsistently across products: correct (price×qty) for the headline
product, wrong (price-only or price×row-count) for the others. A prescriptive recommendation
is built on a wrong number: *"Promote Mochilas with a 10% discount [because they] generated
R$250.00, considerably less than Caneta"* — the premise is off by 2×.

### LP-3 — "High revenue" conflated with "high demand" (MEDIUM; recurrence of Wave-2 class)
*"Aumentar o estoque de Canetas para atender à alta demanda"* — Caneta's revenue is driven by
its high **price** (only 20 units sold), not high demand; Régua sold the same 20 units at a
tiny price. The Advisor infers demand from revenue without hedging — the same over-confident
inference class the 2026-09-06 Wave-2 fixes (items 6/7) targeted for the Advisor. The
guard-rails added then don't cover this revenue≠demand conflation.

### LP-4 — Currency formatting mixes BR symbol with US separators (LOW)
Narrative renders `R$ 24,691.20` — BR symbol (`R$`) with US thousands/decimal (`,`/`.`).
Correct pt-BR would be `R$ 24.691,20`. Locale of the narrative language is correct (pt-BR, per
ADR-036); only the number formatting is mixed.

### LP-5 — Narrative locale is pt-BR while the UI is en-US (HIGH — see progress-message finding)
The Advisor narrative came back in Portuguese because the tenant DB locale resolves to pt-BR,
independent of the en-US UI cookie. This is the same locale-source desync behind the
"Executando pipeline Silver…" progress-message finding (see consolidation P1). Per ADR-036 the
narrative *should* follow the tenant locale, so the narrative being PT is arguably correct-by-
design — but from the user's seat (English UI) it's a jarring inconsistency, and it's the same
root cause as the progress-message regression.

## Verdict
The pipeline gets the **hard part right** (correct headline answer from messy data) but the
**supporting numbers in the synthesized narrative are unreliable** (inconsistent revenue
math) and the recommendations over-claim (revenue→demand). For a product that sells
"recommendations you can act on," LP-2/LP-3 are the substance gap: a user acting on the
Mochila/Caderno figures would act on wrong data.
