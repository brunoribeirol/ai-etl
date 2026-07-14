"""Advisor Agent — prescriptive analytics layer.

Receives all available analysis results and produces:
- recommendations : list of actionable business recommendations
- summary         : executive summary in Portuguese
"""

import json
from typing import Any

import pandas as pd

from ai_etl.core.llm import get_llm

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
  "summary": "2-3 sentence executive summary in Portuguese for a non-technical CEO."
}}

Rules:
- Write the "action", "rationale", and "expected_impact" fields in Portuguese.
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
"""

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _build_data_overview(df: pd.DataFrame) -> str:
    n_rows, n_cols = df.shape
    col_summary = []
    for col in df.columns[:10]:  # cap at 10 columns
        dtype = str(df[col].dtype)
        nulls = int(df[col].isna().sum())
        col_summary.append(f"  {col} ({dtype}, {nulls} nulls)")
    more = f"\n  ... e mais {n_cols - 10} colunas" if n_cols > 10 else ""
    return f"{n_rows} linhas × {n_cols} colunas\nColunas:\n" + "\n".join(col_summary) + more


def _build_gold_context(gold: dict[str, Any]) -> str:
    if not gold or gold.get("error"):
        return "Análise descritiva não disponível."

    parts: list[str] = []
    if gold.get("narrative"):
        parts.append(f"Narrativa: {gold['narrative']}")

    gold_df = gold.get("gold_df")
    if isinstance(gold_df, pd.DataFrame) and not gold_df.empty:
        parts.append(f"Dados agregados:\n{gold_df.head(10).to_string(index=False)}")

    return "\n".join(parts) if parts else "Análise descritiva não disponível."


def _build_science_context(science: dict[str, Any]) -> str:
    if not science or science.get("error"):
        return "Análise preditiva não disponível."

    parts: list[str] = []
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

    return "\n".join(parts) if parts else "Análise preditiva não disponível."


def run_advisor(
    df: pd.DataFrame,
    business_question: str,
    gold_result: dict[str, Any],
    science_result: dict[str, Any],
) -> dict[str, Any]:
    """Generate prescriptive recommendations from all available analysis.

    Returns a dict with keys:
        recommendations : list[dict] — each has action, rationale, priority, expected_impact
        summary         : str — executive summary in Portuguese
        error           : str or None
    """
    data_overview = _build_data_overview(df)
    gold_context = _build_gold_context(gold_result)
    science_context = _build_science_context(science_result)

    prompt = _PROMPT_TEMPLATE.format(
        question=business_question,
        data_overview=data_overview,
        gold_context=gold_context,
        science_context=science_context,
    )

    llm = get_llm()

    for attempt in range(1, 3):
        response = llm.invoke(prompt)
        raw = str(response.content).strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            lines = raw.splitlines()
            start = 1
            end = len(lines)
            if lines[-1].strip() == "```":
                end -= 1
            raw = "\n".join(lines[start:end]).strip()

        try:
            parsed = json.loads(raw)
            recommendations: list[dict[str, Any]] = parsed.get("recommendations", [])
            summary: str = parsed.get("summary", "")

            # Validate and normalize priority field
            valid_priorities = {"high", "medium", "low"}
            for rec in recommendations:
                if rec.get("priority") not in valid_priorities:
                    rec["priority"] = "medium"

            # Sort by priority
            recommendations.sort(key=lambda r: _PRIORITY_ORDER.get(r.get("priority", "low"), 2))

            return {
                "recommendations": recommendations,
                "summary": summary,
                "error": None,
            }

        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            if attempt == 1:
                prompt += f"\n\nYour previous response was not valid JSON. Error: {exc}. Try again."

    return {
        "recommendations": [],
        "summary": "Não foi possível gerar recomendações automaticamente.",
        "error": "JSON parsing failed after 2 attempts",
    }
