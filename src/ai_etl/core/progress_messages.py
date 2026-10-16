"""Locale-aware progress messages for `services/pipeline_service.py`'s
`progress_callback` calls (Sprint 25/ADR-036 gap-closing fix, 2026-09-08).

Real bug found live: `core/locale.py` (ADR-036) already resolves a
per-tenant locale and threads it through the Transformer's date-parsing
convention and every Analyst/Science/Advisor/Planner prompt's *narrative*
language — but the execution-progress messages
`agent-progress.tsx`/`analysis-section.tsx` render live while a pipeline
runs were never touched by that work. They stayed 100% hardcoded
Portuguese strings regardless of the tenant's locale or the frontend's own
`en-US`/`pt-BR` UI language, so an English-UI user watched Portuguese
"Executando pipeline Silver..."/"Calculando KPIs..." scroll past during
every run.

Same shape as `core/locale.py`'s own catalogs (`LOCALE_METADATA`,
`narrative_language_instruction`) — a `dict[locale, dict[key, template]]`
plus a lookup helper — kept in its own module rather than added to
`locale.py` because these are UI-facing progress strings with `.format()`
placeholders, a different concern than the LLM-prompt-facing metadata
`locale.py` already owns.
"""

from __future__ import annotations

from typing import Any

from ai_etl.core.locale import DEFAULT_LOCALE, resolve_locale

# Emoji + label + locale-aware description for each Silver/LangGraph node as
# it streams. `label` ("Orchestrator", "Extractor", ...) is a proper noun —
# the same across every locale, same convention as `agent-progress.tsx`'s
# `PHASES` labels ("Silver", "Planner", "Advisor") staying untranslated.
AGENT_STEP_LABELS: dict[str, tuple[str, str]] = {
    "orchestrator": ("🧠", "Orchestrator"),
    "extractor": ("📥", "Extractor"),
    "transformer": ("⚙️", "Transformer"),
    "quality": ("🔍", "Quality"),
    "loader": ("💾", "Loader"),
}

_AGENT_STEP_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "pt-BR": {
        "orchestrator": "Planejando o pipeline...",
        "extractor": "Extraindo e inspecionando os dados...",
        "transformer": "Transformando e limpando os dados (Silver)...",
        "quality": "Verificando qualidade dos dados...",
        "loader": "Persistindo os dados limpos...",
    },
    "en-US": {
        "orchestrator": "Planning the pipeline...",
        "extractor": "Extracting and inspecting the data...",
        "transformer": "Transforming and cleaning the data (Silver)...",
        "quality": "Checking data quality...",
        "loader": "Persisting the cleaned data...",
    },
}

_DEFAULT_STEP_DESCRIPTION: dict[str, str] = {
    "pt-BR": "Processando...",
    "en-US": "Processing...",
}


def agent_step(locale: str, node_name: str) -> tuple[str, str, str]:
    """`(emoji, label, description)` for one Silver/LangGraph node, in the
    caller's locale. Falls back to a generic "Processing..." description
    for any node name not in `AGENT_STEP_LABELS` (same soft-fail shape the
    old module-level `AGENT_STEPS.get(node_name, ...)` default had)."""
    locale = resolve_locale(locale)
    emoji, label = AGENT_STEP_LABELS.get(node_name, ("⚡", node_name))
    desc = _AGENT_STEP_DESCRIPTIONS.get(locale, _AGENT_STEP_DESCRIPTIONS[DEFAULT_LOCALE]).get(
        node_name, _DEFAULT_STEP_DESCRIPTION.get(locale, _DEFAULT_STEP_DESCRIPTION[DEFAULT_LOCALE])
    )
    return emoji, label, desc


# `{placeholder}` names match exactly what each call site in
# `pipeline_service.py` passes as `**kwargs` to `progress_message()` below —
# keep both in sync when adding a new message.
_MESSAGES: dict[str, dict[str, str]] = {
    "pt-BR": {
        "silver_start": "⚡ Executando pipeline Silver...",
        "silver_done": "✅ Silver concluído em {total_time}s",
        "silver_failed": "❌ Silver falhou: {err}",
        "silver_unknown_error": "Erro desconhecido",
        "gold_start": "🏅 Gold: {task_question}",
        "gold_analyst_start": "🤖 **Analyst Agent**: calculando KPIs e insights...",
        "gold_warning": "⚠️ Gold concluído com aviso ({elapsed}s)",
        "gold_sanity_warning": "⚠️ Gold pronto com ressalva de sanity-check ({elapsed}s)",
        "gold_done": "✅ Gold pronto em {elapsed}s ({attempts} tentativa(s))",
        "science_start": "🔬 Science: {task_question}",
        "science_agent_start": "🤖 **Science Agent**: treinando modelo e gerando previsões...",
        "science_warning": "⚠️ Science concluído com aviso ({elapsed}s)",
        "science_sanity_warning": "⚠️ {model_type} treinado com ressalva de sanity-check ({elapsed}s)",
        "science_done": "✅ {model_type} treinado em {elapsed}s ({attempts} tentativa(s))",
        "science_default_model_type": "Modelo",
        "repair_start": "🔧 {label}, reparo automático: {task_question}",
        "repair_trying": "A sub-análise falhou; tentando uma versão simplificada da pergunta...",
        "repair_success": "✅ Reparo automático funcionou",
        "repair_failed": "⚠️ Reparo também falhou",
        "planner_start": "🧭 Planner: decompondo a pergunta em sub-análises...",
        "planner_done": "✅ {count} sub-análise(s) planejada(s)",
        "planner_task_line": "- ({type}) {question}",
        "advisor_start": "🎯 Advisor: recomendações prescritivas...",
        "advisor_agent_start": "🤖 **Advisor Agent**: sintetizando análises e gerando recomendações...",
        "advisor_warning": "⚠️ Advisor com aviso ({elapsed}s)",
        "advisor_done": "✅ {count} recomendações geradas em {elapsed}s",
    },
    "en-US": {
        "silver_start": "⚡ Running Silver pipeline...",
        "silver_done": "✅ Silver completed in {total_time}s",
        "silver_failed": "❌ Silver failed: {err}",
        "silver_unknown_error": "Unknown error",
        "gold_start": "🏅 Gold: {task_question}",
        "gold_analyst_start": "🤖 **Analyst Agent**: computing KPIs and insights...",
        "gold_warning": "⚠️ Gold completed with a warning ({elapsed}s)",
        "gold_sanity_warning": "⚠️ Gold ready with a sanity-check caveat ({elapsed}s)",
        "gold_done": "✅ Gold ready in {elapsed}s ({attempts} attempt(s))",
        "science_start": "🔬 Science: {task_question}",
        "science_agent_start": "🤖 **Science Agent**: training model and generating forecasts...",
        "science_warning": "⚠️ Science completed with a warning ({elapsed}s)",
        "science_sanity_warning": "⚠️ {model_type} trained with a sanity-check caveat ({elapsed}s)",
        "science_done": "✅ {model_type} trained in {elapsed}s ({attempts} attempt(s))",
        "science_default_model_type": "Model",
        "repair_start": "🔧 {label}, automatic repair: {task_question}",
        "repair_trying": "The sub-analysis failed; trying a simplified version of the question...",
        "repair_success": "✅ Automatic repair worked",
        "repair_failed": "⚠️ Repair also failed",
        "planner_start": "🧭 Planner: breaking the question down into sub-analyses...",
        "planner_done": "✅ {count} sub-analysis(es) planned",
        "planner_task_line": "- ({type}) {question}",
        "advisor_start": "🎯 Advisor: prescriptive recommendations...",
        "advisor_agent_start": "🤖 **Advisor Agent**: synthesizing analyses and generating recommendations...",
        "advisor_warning": "⚠️ Advisor completed with a warning ({elapsed}s)",
        "advisor_done": "✅ {count} recommendation(s) generated in {elapsed}s",
    },
}


def progress_message(locale: str, key: str, **kwargs: Any) -> str:
    """Look up and format one progress message in the caller's locale.

    Falls back to `DEFAULT_LOCALE`'s template if `locale` isn't recognized
    (mirrors `core/locale.py::resolve_locale`'s soft-fail convention), and
    raises `KeyError` if `key` itself is missing from both locales — a
    programming error (typo'd key), not a runtime condition to swallow
    silently the way an unknown locale is.
    """
    locale = resolve_locale(locale)
    catalog = _MESSAGES.get(locale, _MESSAGES[DEFAULT_LOCALE])
    template = catalog.get(key) or _MESSAGES[DEFAULT_LOCALE][key]
    return template.format(**kwargs)
