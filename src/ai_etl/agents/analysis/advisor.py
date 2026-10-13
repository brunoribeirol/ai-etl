"""Advisor Agent — prescriptive analytics layer.

Receives all available analysis results and produces:
- recommendations : list of actionable business recommendations
- summary         : executive summary in Portuguese
"""

import json
from typing import Any

import pandas as pd

from ai_etl.agents._llm_codegen import strip_code_fences
from ai_etl.core.analysis_types import (
    AdvisorResult,
    GoldResult,
    OutputSanityCheck,
    ScienceResult,
    TokenUsage,
)
from ai_etl.core.llm import extract_token_usage, get_llm, invoke_llm, response_text, sum_token_usage
from ai_etl.core.locale import DEFAULT_LOCALE, narrative_language_instruction, resolve_locale

_PROMPT_TEMPLATE = """\
You are a senior business advisor with deep expertise in data-driven strategy.

## Context

**Business question:** "{question}"

**Data overview:**
{data_overview}

**Descriptive analysis (Gold layer):**
{gold_context}

**Predictive analysis (Science layer):**
{science_context}

## Your task

Based on all the evidence above, generate 3 to 5 **specific, actionable business recommendations**.

Each recommendation must:
- Be directly grounded in the data (cite specific numbers, columns, or patterns)
- Be concrete enough to be implemented this week (not vague)
- Include the expected business impact

Respond ONLY with valid JSON (no markdown fences) in this exact structure:
{{
  "recommendations": [
    {{
      "action": "...",
      "rationale": "...",
      "priority": "high" | "medium" | "low",
      "expected_impact": "..."
    }}
  ],
  "summary": "2-3 sentence executive summary for a non-technical CEO."
}}

## Language for this tenant
{language_instruction}

Rules:
- Write the "action", "rationale", "expected_impact", and "summary" fields in the tenant's
  language above.
- "priority" must be exactly one of: "high", "medium", "low".
- Be specific: mention actual column names, product names, or numbers from the data.
- Do NOT invent data that was not provided.
- Do NOT restate the business question or propose running the analysis that was already
  requested (e.g. "investigar X", "construir Y", "implementar um ranking") as if it were a
  recommendation — the analysis has already been done above; you must recommend a CONCRETE
  BUSINESS ACTION based on its results, not a research task.
- If the descriptive or predictive analysis above is unavailable (says "não disponível"),
  do NOT recommend "running the analysis" either — instead, only make recommendations that
  are safely grounded in the data overview alone, and say plainly in the summary that some
  recommendations are limited by missing upstream analysis.
- If a sub-task above carries a "⚠️ Known data-consistency warning" (ADR-037), do NOT present
  a recommendation based on the flagged number without acknowledging the warning — either
  avoid grounding a recommendation in that specific figure, or explicitly caveat it in the
  "rationale" and mention the uncertainty in the "summary".
- If a recommendation is grounded in a segment, group, or breakdown with a small sample size
  (roughly fewer than 5 rows/customers/records backing that specific number), do NOT present
  it at "high" priority as if it were as reliable as a finding from the full dataset —
  downgrade its priority (to "medium" or "low") and say in the "rationale" that the sample
  behind it is small, so the reader doesn't act on a tiny-N pattern with full confidence.
- Column names that sound like a specific calculated metric ("profit_margin", "revenue",
  "conversion_rate") are NOT guaranteed to actually be that metric — the column could hold
  raw values under a misleading name (e.g. a "profit_margin" column that is really unit
  price, never divided by anything). Do not assert what a column semantically represents
  beyond what the data overview/schema actually says about it; if you're inferring the
  column's real-world meaning from its name alone, say so as an assumption in the
  "rationale" rather than stating it as settled fact.
- Apply ONE consistent method to any metric you cite across items. If you report "total
  revenue" (or any aggregate) for one product/segment, compute it the SAME way for every
  other one you cite in the same set of recommendations — do not, e.g., use price × quantity
  for the top item and price-only (or price × row-count) for the others. Inconsistent figures
  across a single recommendation set are worse than no figures: a reader acts on a number that
  isn't comparable to the one next to it. Only cite a numeric figure the analysis above
  actually provides; do not compute a new aggregate yourself from raw rows in your head.
- Do NOT conflate revenue (or total value) with demand, volume, or popularity. A product can
  top revenue because its unit price is high while very few units sold; another can sell many
  units at a low price. If you recommend acting on "high demand", ground it in a quantity/
  count figure, not a revenue figure — and if only revenue is available, say "highest revenue"
  and note that this is driven by price and/or volume, which the data does not separate here.
"""

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# Audit finding M3 (2026-09-10) — deterministic guard for revenue↔demand conflation.
# A prompt rule tells the Advisor not to infer "demand" from a revenue figure, but an
# LLM does not reliably obey it (verified live: a recommendation still said "increase
# inventory to meet demand" grounded in a revenue-only analysis). So detect it after the
# fact: if a recommendation uses demand/volume language while the underlying analysis
# has NO quantity dimension (only value/revenue), append a caveat and downgrade a
# "high" priority — deterministic, and it only ever ADDS a hedge, never removes content.
_DEMAND_LANGUAGE = (
    "demand",
    "demanda",
    "sell the most",
    "vendem mais",
    "mais vendido",
    "best-selling",
    "best selling",
    "most popular",
    "mais popular",
    "high volume",
    "alto volume",
    "units sold",
    "unidades vendidas",
)
# Column-name tokens that mean the analysis actually measured quantity/volume (so a
# demand claim could be legitimately grounded and must NOT be flagged).
_QUANTITY_COLUMN_TOKENS = (
    "quantidade",
    "quantity",
    "qty",
    "units",
    "unidades",
    "count",
    "volume",
)
_CONFLATION_CAVEAT = {
    "pt-BR": (
        " (Observação: baseia-se em receita/valor total, não em unidades vendidas — a "
        "análise não incluiu uma dimensão de quantidade, então 'demanda' aqui pode "
        "refletir preço alto, não volume.)"
    ),
    "en-US": (
        " (Note: grounded in revenue/total value, not units sold — the analysis had no "
        "quantity dimension, so 'demand' here may reflect a high price rather than volume.)"
    ),
}


def _analysis_has_quantity_dimension(
    gold_results: list[GoldResult], science_results: list[ScienceResult]
) -> bool:
    """True if any Gold/Science result's data has a quantity/volume-like column —
    i.e. the analysis actually measured units, not just value/revenue."""
    frames = [g.get("gold_df") for g in gold_results] + [
        s.get("predictions_df") for s in science_results
    ]
    for frame in frames:
        if not isinstance(frame, pd.DataFrame):
            continue
        for col in frame.columns:
            name = str(col).lower()
            if any(tok in name for tok in _QUANTITY_COLUMN_TOKENS):
                return True
    return False


def _hedge_revenue_demand_conflation(
    recommendations: list[dict[str, Any]],
    gold_results: list[GoldResult],
    science_results: list[ScienceResult],
    locale: str,
) -> None:
    """Mutates `recommendations` in place: for any rec that uses demand/volume
    language while the analysis has no quantity dimension, append a caveat to its
    rationale and downgrade a 'high' priority to 'medium'. No-op otherwise."""
    if _analysis_has_quantity_dimension(gold_results, science_results):
        return
    caveat = _CONFLATION_CAVEAT.get(locale, _CONFLATION_CAVEAT[DEFAULT_LOCALE])
    for rec in recommendations:
        text = f"{rec.get('action', '')} {rec.get('rationale', '')}".lower()
        if not any(term in text for term in _DEMAND_LANGUAGE):
            continue
        rationale = rec.get("rationale", "")
        if caveat.strip() not in rationale:
            rec["rationale"] = f"{rationale}{caveat}"
        if rec.get("priority") == "high":
            rec["priority"] = "medium"


_FALLBACK_SUMMARY: dict[str, str] = {
    "pt-BR": "Não foi possível gerar recomendações automaticamente.",
    "en-US": "Automatic recommendations could not be generated.",
}


def _build_data_overview(df: pd.DataFrame) -> str:
    n_rows, n_cols = df.shape
    col_summary = []
    for col in df.columns[:10]:  # cap at 10 columns
        dtype = str(df[col].dtype)
        nulls = int(df[col].isna().sum())
        col_summary.append(f"  {col} ({dtype}, {nulls} nulls)")
    more = f"\n  ... e mais {n_cols - 10} colunas" if n_cols > 10 else ""
    return f"{n_rows} linhas × {n_cols} colunas\nColunas:\n" + "\n".join(col_summary) + more


def _sanity_check_warning(sanity_check: OutputSanityCheck | None) -> str | None:
    """Render a non-"ok" `OutputSanityCheck` (ADR-037) as prompt text, or `None` when
    there is nothing to flag (missing entry, or every check came back "ok").

    The Advisor must see this: `reviewer.py`'s ADR-037 second-pass review (and the
    deterministic checks in `core/output_validation.py`) can catch a genuine
    contradiction between a sub-task's narrative and its data — without this, the
    Advisor's prompt has zero awareness of a warning the system already raised and can
    silently act on the flagged number anyway.
    """
    if not sanity_check or sanity_check.get("severity") == "ok":
        return None
    warnings = [c["detail"] for c in sanity_check.get("checks", []) if c["severity"] != "ok"]
    if not warnings:
        return None
    return "⚠️ Known data-consistency warning for this sub-task:\n" + "\n".join(
        f"  - {w}" for w in warnings
    )


def _build_gold_context(gold_results: list[GoldResult]) -> str:
    """Render every completed Gold sub-task, one block per task question.

    Gold now runs once per sub-task produced by the Planner instead of once for the
    whole business question, so the Advisor must see all of them to cite results the
    single-shot version never had access to.
    """
    blocks: list[str] = []
    for gold in gold_results:
        if not gold or gold.get("error"):
            continue
        task_question = gold.get("task_question", "")
        parts: list[str] = [f"Sub-pergunta: {task_question}"] if task_question else []
        if gold.get("narrative"):
            parts.append(f"Narrativa: {gold['narrative']}")
        gold_df = gold.get("gold_df")
        if isinstance(gold_df, pd.DataFrame) and not gold_df.empty:
            parts.append(f"Dados agregados:\n{gold_df.head(10).to_string(index=False)}")
        warning = _sanity_check_warning(gold.get("sanity_check"))
        if warning:
            parts.append(warning)
        if parts:
            blocks.append("\n".join(parts))

    return "\n\n".join(blocks) if blocks else "Análise descritiva não disponível."


def _build_science_context(science_results: list[ScienceResult]) -> str:
    """Render every completed Science sub-task, one block per task question."""
    blocks: list[str] = []
    for science in science_results:
        if not science or science.get("error"):
            continue
        task_question = science.get("task_question", "")
        parts: list[str] = [f"Sub-pergunta: {task_question}"] if task_question else []
        if science.get("narrative"):
            parts.append(f"Narrativa: {science['narrative']}")

        model_info = science.get("model_info", {})
        if model_info:
            model_type = model_info.get("model_type", "—")
            task = model_info.get("task", "—")
            metrics = model_info.get("metrics", {})
            metrics_str = ", ".join(f"{k}={v}" for k, v in metrics.items())
            parts.append(f"Modelo: {model_type} ({task})")
            if metrics_str:
                parts.append(f"Métricas: {metrics_str}")

        pred_df = science.get("predictions_df")
        if isinstance(pred_df, pd.DataFrame) and not pred_df.empty:
            parts.append(f"Amostra de previsões:\n{pred_df.head(5).to_string(index=False)}")

        warning = _sanity_check_warning(science.get("sanity_check"))
        if warning:
            parts.append(warning)

        if parts:
            blocks.append("\n".join(parts))

    return "\n\n".join(blocks) if blocks else "Análise preditiva não disponível."


def run_advisor(
    df: pd.DataFrame,
    business_question: str,
    gold_results: list[GoldResult],
    science_results: list[ScienceResult],
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
    locale: str = DEFAULT_LOCALE,
) -> AdvisorResult:
    """Generate prescriptive recommendations from all available analysis.

    `gold_results` and `science_results` hold one entry per sub-task produced by the
    Planner (`ai_etl.agents.analysis.planner.plan_analysis_tasks`) — a multi-part business
    question is answered by several independent Gold/Science runs, not a single one.

    Returns an AdvisorResult dict (see ai_etl.core.analysis_types).

    Args:
        llm_provider_override / llm_model_override: Sprint 30/gap-closing (ADR-031
            §5) — see `agents/analysis/planner.py::plan_analysis_tasks`'s identical
            parameters for rationale. `None` (the default) — unchanged behavior.
        locale: Sprint 25 (ADR-036) — the tenant's configured locale, same threading
            pattern as `llm_provider_override` above. Defaults to `DEFAULT_LOCALE`
            ("pt-BR") — unchanged behavior for every existing caller.
    """
    resolved_locale = resolve_locale(locale)
    data_overview = _build_data_overview(df)
    gold_context = _build_gold_context(gold_results)
    science_context = _build_science_context(science_results)

    prompt = _PROMPT_TEMPLATE.format(
        question=business_question,
        data_overview=data_overview,
        gold_context=gold_context,
        science_context=science_context,
        language_instruction=narrative_language_instruction(resolved_locale),
    )

    llm = get_llm(provider=llm_provider_override, model=llm_model_override)
    attempt_usages: list[TokenUsage] = []

    for attempt in range(1, 3):
        response = invoke_llm(llm, prompt, llm_provider_override)
        attempt_usages.append(extract_token_usage(response))
        raw = strip_code_fences(response_text(response))

        try:
            parsed = json.loads(raw)
            recommendations: list[dict[str, Any]] = parsed.get("recommendations", [])
            summary: str = parsed.get("summary", "")

            # Validate and normalize priority field
            valid_priorities = {"high", "medium", "low"}
            for rec in recommendations:
                if rec.get("priority") not in valid_priorities:
                    rec["priority"] = "medium"

            # Audit M3 (2026-09-10): deterministic revenue↔demand conflation guard —
            # caveats + downgrades recs that infer demand from a revenue-only analysis.
            # Runs before the sort so a downgraded "high" re-orders correctly.
            _hedge_revenue_demand_conflation(
                recommendations, gold_results, science_results, resolved_locale
            )

            # Sort by priority
            recommendations.sort(key=lambda r: _PRIORITY_ORDER.get(r.get("priority", "low"), 2))

            return {
                "recommendations": recommendations,  # type: ignore[typeddict-item]  # validated above
                "summary": summary,
                "error": None,
                "tokens": sum_token_usage(*attempt_usages),
            }

        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            if attempt == 1:
                prompt += f"\n\nYour previous response was not valid JSON. Error: {exc}. Try again."

    return {
        "recommendations": [],
        "summary": _FALLBACK_SUMMARY.get(resolved_locale, _FALLBACK_SUMMARY[DEFAULT_LOCALE]),
        "error": "JSON parsing failed after 2 attempts",
        "tokens": sum_token_usage(*attempt_usages),
    }
