"""Pipeline orchestration service — sequences Silver -> Planner -> Gold/Science ->
Advisor without any dependency on the presentation layer.

Extracted from `app.py`, which used to inline this whole sequence inside a Streamlit
button handler, mixing agent orchestration with `st.status`/`st.session_state` calls.
That made the orchestration untestable without booting Streamlit and unusable from
any future caller (a worker, the CLI, a script) that isn't the Streamlit app.

Every function here accepts a `ProgressCallback` — a plain `(stage, message) -> None`
callable — instead of talking to `st.status` directly. Callers that want live
progress (e.g. `app.py`) supply an adapter; callers that don't (tests, batch scripts)
can omit it, since the default is a no-op.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Optional, cast

import pandas as pd

from ai_etl.agents.analysis.advisor import run_advisor
from ai_etl.agents.analysis.analyst import run_analyst
from ai_etl.agents.analysis.planner import plan_analysis_tasks
from ai_etl.agents.analysis.reviewer import review_gold_result, review_science_result
from ai_etl.agents.analysis.science import run_science
from ai_etl.agents.pipeline.loader import loader_node
from ai_etl.audit.db import (
    get_locale,
    get_run_status_and_pipeline,
    get_saved_pipeline,
    get_saved_pipeline_llm_config,
    load_full_result,
    mark_pipeline_approved,
    record_pipeline_health,
    save_analysis,
    save_run,
    save_stage_latencies,
)
from ai_etl.audit.logger import log_action
from ai_etl.core.analysis_types import (
    AdvisorResult,
    AnalysisRunResult,
    GoldResult,
    ScienceResult,
    TokenUsage,
)
from ai_etl.core.graph import build_graph
from ai_etl.core.llm import get_model_name, is_llm_review_enabled, sum_token_usage
from ai_etl.core.locale import DEFAULT_LOCALE
from ai_etl.core.output_validation import append_check, check_gold_output, check_science_output
from ai_etl.core.state import PipelineState, initial_state

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str], None]

# Emoji + label + short description shown for each Silver/LangGraph node as it
# streams. Also consumed by app.py (imported, not duplicated) to render the
# "Pipeline" results tab, which needs the same emoji/label pairing.
AGENT_STEPS: dict[str, tuple[str, str, str]] = {
    "orchestrator": ("🧠", "Orchestrator", "Planejando o pipeline..."),
    "extractor": ("📥", "Extractor", "Extraindo e inspecionando os dados..."),
    "transformer": ("⚙️", "Transformer", "Transformando e limpando os dados (Silver)..."),
    "quality": ("🔍", "Quality", "Verificando qualidade dos dados..."),
    "loader": ("💾", "Loader", "Persistindo os dados limpos..."),
}


def _noop_progress(stage: str, message: str) -> None:
    return None


def _build_approval_policy(pipeline: dict[str, Any]) -> dict[str, Any]:
    """Sprint 27 (ADR-028) — `saved_pipelines` row -> `PipelineState["approval_policy"]`.

    `last_approved_at` is carried as an ISO string (not the raw `datetime`) since
    this dict crosses into `PipelineState`, which is JSON-serialized wholesale by
    `audit.db._make_serializable`/`save_run` — keeping it JSON-safe from the start
    avoids a silent `str(datetime)` fallback there. `agents/pipeline/loader.py::_is_write_gated`
    only ever checks it for `None`-ness, never parses it back into a `datetime`.
    """
    last_approved_at = pipeline.get("last_approved_at")
    return {
        "require_approval": bool(pipeline.get("require_approval")),
        "threshold_rows": pipeline.get("approval_threshold_rows"),
        "last_approved_at": last_approved_at.isoformat() if last_approved_at else None,
    }


def run_silver_pipeline(
    spec: str,
    run_dir: str,
    progress_callback: ProgressCallback = _noop_progress,
    tenant_id: str | None = None,
    saved_pipeline_id: str | None = None,
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
) -> PipelineState:
    """Run the Silver LangGraph (Orchestrator -> Extractor -> Transformer -> Quality
    -> Loader) to completion and persist the resulting state via `save_run`.

    Mirrors the previous `app.py::_run_silver_pipeline` node-by-node, including the
    per-node `agent_timings` computation (each entry is ~0 by construction — timed
    from immediately before to immediately after the state update, not across the
    node's actual execution — a pre-existing quirk kept as-is rather than fixed here).

    Args:
        tenant_id: Sprint A session-scoping stopgap forwarded to `save_run` — the
            browser session's UUID (see `app.py::_get_session_id`), not a real
            tenant/account. Defaults to `None` for backward compatibility.
        saved_pipeline_id: Sprint 17 (ADR-017) — forwarded to `save_run` so a
            scheduled fire (`services/scheduler.py`) can be grouped with the
            rest of its saved pipeline's run history. `None` for every avulso
            (one-off) run — the default, and the only value most callers pass.
            Sprint 14 (ADR-018) also relies on this to later find this run's
            predecessor for drift detection. Sprint 16 (ADR-023) also uses it
            here to look up that saved pipeline's `quality_rules`.
        llm_provider_override/llm_model_override: gap-closing fix (2026-08-25
            audit, Wave 4) — a caller-supplied override for an avulso run, e.g.
            `POST /runs`' `ModelPicker` selection, which has no `saved_pipeline_id`
            to resolve an override from the DB. Ignored (overwritten) whenever a
            real saved-pipeline override is found below — a saved pipeline's own
            configured override always wins over whatever a caller happened to
            pass, matching the pre-existing behavior for that path exactly.
    """
    run_id = str(uuid.uuid4())
    # Sprint 16 (ADR-023) — a saved pipeline's own operator-defined quality rules,
    # resolved here (not inside `quality_node`, which stays a pure function of
    # `PipelineState`) and carried into the graph via `initial_state`. Only a
    # scheduled fire has both a `saved_pipeline_id` and the real owning `tenant_id`
    # (confirmed in `services/scheduler.py` — see ADR-023) — an avulso run always
    # gets `[]`, unchanged fixed-checks-only behavior.
    # Sprint 27 (ADR-028) — same lookup, same "only a scheduled fire has both a
    # saved_pipeline_id and the real tenant_id" gate: this saved pipeline's own
    # write-approval policy, resolved here (not inside `loader_node`, which stays
    # a pure function of `PipelineState`) and carried into the graph via
    # `initial_state`. An avulso run always gets `None` — never gated.
    # Sprint 30/gap-closing (ADR-031 §5) — same lookup gate as quality_rules/
    # approval_policy above: this saved pipeline's own LLM provider/model override
    # (Sprint 30, ADR-031 §3), resolved here (not inside any `agents/pipeline/*.py`
    # node, which stay pure functions of `PipelineState`) and carried into the
    # graph via `initial_state`. A separate `get_saved_pipeline_llm_config` call
    # (not folded into `get_saved_pipeline`'s dict) — `audit/db.py`'s Sprint 30
    # append-only constraint kept the two functions standalone; no reason to widen
    # `get_saved_pipeline`'s return shape just because a caller now wants both.
    custom_quality_rules: list[dict[str, Any]] = []
    approval_policy: Optional[dict[str, Any]] = None
    if saved_pipeline_id is not None and tenant_id is not None:
        pipeline = get_saved_pipeline(saved_pipeline_id, tenant_id)
        if pipeline is not None:
            custom_quality_rules = pipeline.get("quality_rules", [])
            approval_policy = _build_approval_policy(pipeline)
        llm_config = get_saved_pipeline_llm_config(saved_pipeline_id, tenant_id)
        if llm_config is not None:
            llm_provider_override = llm_config.get("llm_provider")
            llm_model_override = llm_config.get("llm_model")
    # Sprint 25 (ADR-036) — unlike quality_rules/approval_policy/the LLM override above,
    # locale is a per-*tenant* setting (users.locale), not per-saved-pipeline: resolved
    # whenever a real tenant_id is present, regardless of saved_pipeline_id, so every
    # avulso run also gets its tenant's configured locale.
    #
    # Real regression found post-merge (2026-08-23): `get_locale()` has no fail-safe
    # against an unreachable/unconfigured database (unlike the saved_pipeline lookups
    # above, which only run when saved_pipeline_id is also set) — a DB hiccup, or any
    # caller with no APP_DATABASE_URL at all (e.g. case_study/scripts/model_comparison.py,
    # which deliberately bypasses persistence), previously crashed the *entire* run
    # before a single agent ran, for a cosmetic i18n setting. Narrating the run in the
    # wrong language is a much smaller failure than never running it — fall back to
    # DEFAULT_LOCALE and log, don't propagate.
    locale = DEFAULT_LOCALE
    if tenant_id is not None:
        try:
            locale = get_locale(tenant_id)
        except Exception:  # noqa: BLE001 — see comment above: never let this block a run
            logger.warning(
                "run_silver_pipeline: failed to resolve tenant locale, "
                "falling back to default (%r)",
                DEFAULT_LOCALE,
                exc_info=True,
            )
    state = initial_state(
        spec=spec,
        run_id=run_id,
        custom_quality_rules=custom_quality_rules,
        approval_policy=approval_policy,
        llm_provider_override=llm_provider_override,
        llm_model_override=llm_model_override,
        locale=locale,
    )
    graph = build_graph()

    final_state: dict[str, Any] = dict(state)
    agent_timings: dict[str, float] = {}

    progress_callback("silver", "⚡ Executando pipeline Silver...")
    t_total = time.time()
    for chunk in graph.stream(state):
        node_name = list(chunk.keys())[0]
        partial = chunk[node_name]
        t_node = time.time()
        final_state.update(partial)

        emoji, label, desc = AGENT_STEPS.get(node_name, ("⚡", node_name, "Processando..."))
        agent_timings[node_name] = round(time.time() - t_node, 2)
        elapsed = round(time.time() - t_total, 1)
        progress_callback("silver", f"{emoji} **{label}** — {desc} *({elapsed}s)*")

    overall = final_state.get("status", "unknown")
    total_time = round(time.time() - t_total, 1)
    if overall == "completed":
        progress_callback("silver", f"✅ Silver concluído em {total_time}s")
    else:
        err = final_state.get("error") or "Erro desconhecido"
        progress_callback("silver", f"❌ Silver falhou: {err[:80]}")

    final_state["_agent_timings"] = agent_timings
    final_state["_total_time"] = total_time

    save_run(
        cast(PipelineState, final_state),
        log_dir=run_dir,
        tenant_id=tenant_id,
        saved_pipeline_id=saved_pipeline_id,
    )

    # ADR-007: per-LangGraph-node wall-clock durations, captured by core/graph.py's
    # `_timed()` wrapper into state["stage_durations"]. A no-op if the graph didn't
    # populate it (e.g. a fake/stubbed graph in tests).
    save_stage_latencies(
        run_id,
        "silver",
        tenant_id,
        final_state.get("stage_durations", {}),
        log_dir=run_dir,
    )

    return cast(PipelineState, final_state)


def _record_stage_call(
    stage_log: "list[dict[str, Any]] | None", agent: str, elapsed: float, error: str | None
) -> None:
    """Append one Analyst/Science call's timing into `stage_log`, if the caller
    passed one (per-call opt-in — same pattern as `progress_callback`, so callers
    that don't care about latency persistence, e.g. tests, can omit it).

    `timed_out` is inferred from the error message's ADR-007 timeout phrasing
    (`f"Execution exceeded {N}s — simplify the computation"`, set by
    analyst.py/science.py's sandbox-timeout branch) rather than threaded through
    as a separate field, since `run_analyst`/`run_science`'s return contract
    (GoldResult/ScienceResult) has no dedicated `timed_out` key.
    """
    if stage_log is None:
        return
    stage_log.append(
        {
            "stage": agent,
            "duration_seconds": elapsed,
            "timed_out": bool(error) and "Execution exceeded" in (error or ""),
        }
    )


def _log_llm_override_if_used(
    agent: str, run_id: str, llm_provider_override: str | None, llm_model_override: str | None
) -> None:
    """Sprint 30/gap-closing (ADR-031 §5) — visibility into which provider/model
    actually ran an Analyst/Science/Advisor/Planner call, for the agentic BI layer
    that runs outside the LangGraph/PipelineState (see this module's docstring) and
    therefore can't call `audit/logger.py::log_action` the way `agents/pipeline/*.py`
    nodes do. Standard logging is the established substitute this module already
    uses for that layer's other agent-level visibility (see `run_gold_analysis`'s/
    `run_advisor_analysis`'s `logger.warning` calls on failure) — a no-op when no
    override is configured, so this stays silent for every deployment-default run.
    """
    if llm_provider_override or llm_model_override:
        logger.info(
            "%s: llm override in use for run_id=%s — provider=%s model=%s",
            agent,
            run_id,
            llm_provider_override,
            llm_model_override,
        )


def run_gold_analysis(
    silver_df: pd.DataFrame,
    task_question: str,
    progress_callback: ProgressCallback = _noop_progress,
    stage: str = "gold",
    stage_log: "list[dict[str, Any]] | None" = None,
    run_id: str = "",
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
    locale: str = DEFAULT_LOCALE,
) -> GoldResult:
    """Run one Gold (descriptive) sub-task via the Analyst agent.

    `stage_log`: optional list this call appends its ADR-007 latency entry to
    (see `_record_stage_call`) — omitted by default, populated by
    `run_analysis_tasks` when the caller wants Analyst/Science latencies
    persisted via `save_stage_latencies`.

    `run_id`/`llm_provider_override`/`llm_model_override`: Sprint 30/gap-closing
    (ADR-031 §5) — see `_log_llm_override_if_used`. `run_id` is only used for that
    log line; it's not forwarded to `run_analyst` itself.

    `locale`: Sprint 25 (ADR-036) — the tenant's configured locale, forwarded to
    `run_analyst` for the narrative's output language. Defaults to `DEFAULT_LOCALE`.
    """
    progress_callback(stage, f"🏅 Gold — {task_question}")
    progress_callback(stage, "🤖 **Analyst Agent** — Calculando KPIs e insights...")
    _log_llm_override_if_used("analyst", run_id, llm_provider_override, llm_model_override)
    t0 = time.monotonic()
    result = run_analyst(
        silver_df, task_question, llm_provider_override, llm_model_override, locale
    )
    elapsed = time.monotonic() - t0
    _record_stage_call(stage_log, "analyst", elapsed, result.get("error"))
    elapsed_display = round(elapsed, 1)
    attempts = result.get("attempts", 1)

    if result["error"]:
        # Agentic BI layer runs outside the LangGraph/PipelineState, so there's no
        # `log_action()` audit trail here (see module docstring) — without this, a
        # failed sub-task was only ever visible transiently via `progress_callback`,
        # never in a queryable log a Sprint-28-era testers-report investigation could
        # replay.
        logger.warning(
            "run_gold_analysis: analyst failed for task_question=%r after %s attempt(s): %s",
            task_question,
            attempts,
            result["error"],
        )
        progress_callback(stage, f"⚠️ Gold concluído com aviso ({elapsed_display}s)")
        return {**result, "task_question": task_question}

    # Sprint 21 (ADR-026): sanity-check a successful result against the Silver data
    # it was derived from — a result that "ran" (ADR-007) is not necessarily correct.
    sanity_check = check_gold_output(result["gold_df"], silver_df, result.get("narrative", ""))
    tokens = result["tokens"]
    # ADR-037 (Sprint 21 follow-up): opt-in second LLM pass, additive to the
    # deterministic checks above — see core.llm.is_llm_review_enabled's docstring
    # for why this is a global env var, not a per-pipeline setting.
    if is_llm_review_enabled():
        review_entries, review_tokens = review_gold_result(
            task_question,
            result.get("narrative", ""),
            result["gold_df"],
            llm_provider_override,
            llm_model_override,
        )
        tokens = sum_token_usage(tokens, review_tokens)
        for review_entry in review_entries:
            sanity_check = append_check(sanity_check, review_entry)
    if sanity_check["severity"] != "ok":
        progress_callback(stage, f"⚠️ Gold pronto com ressalva de sanity-check ({elapsed_display}s)")
    else:
        progress_callback(stage, f"✅ Gold pronto em {elapsed_display}s ({attempts} tentativa(s))")
    return {
        **result,
        "task_question": task_question,
        "sanity_check": sanity_check,
        "tokens": tokens,
    }


def run_science_analysis(
    silver_df: pd.DataFrame,
    task_question: str,
    progress_callback: ProgressCallback = _noop_progress,
    stage: str = "science",
    stage_log: "list[dict[str, Any]] | None" = None,
    run_id: str = "",
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
    locale: str = DEFAULT_LOCALE,
) -> ScienceResult:
    """Run one Science (diagnostic/predictive) sub-task via the Science agent.

    `stage_log`: see `run_gold_analysis`.

    `run_id`/`llm_provider_override`/`llm_model_override`: Sprint 30/gap-closing
    (ADR-031 §5) — see `run_gold_analysis`'s identical parameters.

    `locale`: Sprint 25 (ADR-036) — see `run_gold_analysis`'s identical parameter.
    """
    progress_callback(stage, f"🔬 Science — {task_question}")
    progress_callback(stage, "🤖 **Science Agent** — Treinando modelo e gerando previsões...")
    _log_llm_override_if_used("science", run_id, llm_provider_override, llm_model_override)
    t0 = time.monotonic()
    result = run_science(
        silver_df, task_question, llm_provider_override, llm_model_override, locale
    )
    elapsed = time.monotonic() - t0
    _record_stage_call(stage_log, "science", elapsed, result.get("error"))
    elapsed_display = round(elapsed, 1)
    attempts = result.get("attempts", 1)

    if result["error"]:
        # See run_gold_analysis's comment above — same rationale.
        logger.warning(
            "run_science_analysis: science agent failed for task_question=%r after %s "
            "attempt(s): %s",
            task_question,
            attempts,
            result["error"],
        )
        progress_callback(stage, f"⚠️ Science concluído com aviso ({elapsed_display}s)")
        return {**result, "task_question": task_question}

    # Sprint 21 (ADR-026): sanity-check a successful result against the Silver data
    # it was derived from — a result that "ran" (ADR-007) is not necessarily correct.
    model_info = result.get("model_info", {})
    model_info_dict: dict[str, Any] = dict(model_info) if isinstance(model_info, dict) else {}
    sanity_check = check_science_output(result["predictions_df"], model_info_dict, silver_df)
    tokens = result["tokens"]
    # ADR-037 (Sprint 21 follow-up): see run_gold_analysis's identical block above.
    if is_llm_review_enabled():
        review_entries, review_tokens = review_science_result(
            task_question,
            result.get("narrative", ""),
            result["predictions_df"],
            model_info_dict,
            llm_provider_override,
            llm_model_override,
        )
        tokens = sum_token_usage(tokens, review_tokens)
        for review_entry in review_entries:
            sanity_check = append_check(sanity_check, review_entry)
    model_type = model_info_dict.get("model_type", "Modelo")
    if sanity_check["severity"] != "ok":
        progress_callback(
            stage, f"⚠️ {model_type} treinado com ressalva de sanity-check ({elapsed_display}s)"
        )
    else:
        progress_callback(
            stage, f"✅ {model_type} treinado em {elapsed_display}s ({attempts} tentativa(s))"
        )
    return {
        **result,
        "task_question": task_question,
        "sanity_check": sanity_check,
        "tokens": tokens,
    }


def run_gold_with_repair(
    silver_df: pd.DataFrame,
    task_question: str,
    progress_callback: ProgressCallback = _noop_progress,
    stage: str = "gold",
    stage_log: "list[dict[str, Any]] | None" = None,
    run_id: str = "",
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
    locale: str = DEFAULT_LOCALE,
) -> GoldResult:
    """Run a Gold sub-task; if it fails outright, try once more with a simplified
    fallback question before giving up.

    `run_analyst` already retries the SAME question up to 3 times internally — this is
    a different failure mode: the question itself may be too specific/complex for the
    LLM to translate into working code at all, so the fallback rephrases it instead of
    repeating it verbatim.

    `run_id`/`llm_provider_override`/`llm_model_override`: Sprint 30/gap-closing
    (ADR-031 §5) — see `run_gold_analysis`'s identical parameters.

    `locale`: Sprint 25 (ADR-036) — see `run_gold_analysis`'s identical parameter.
    """
    result = run_gold_analysis(
        silver_df,
        task_question,
        progress_callback,
        stage,
        stage_log,
        run_id,
        llm_provider_override,
        llm_model_override,
        locale,
    )
    if not result.get("error"):
        return result

    fallback_question = (
        f"Resuma de forma simples os principais números relacionados a: {task_question}"
    )
    repair_stage = f"{stage}:repair"
    progress_callback(repair_stage, f"🔧 Gold — reparo automático: {task_question}")
    progress_callback(
        repair_stage, "A sub-análise falhou; tentando uma versão simplificada da pergunta..."
    )
    repaired = run_gold_analysis(
        silver_df,
        fallback_question,
        progress_callback,
        repair_stage,
        stage_log,
        run_id,
        llm_provider_override,
        llm_model_override,
        locale,
    )
    progress_callback(
        repair_stage,
        "✅ Reparo automático funcionou" if not repaired.get("error") else "⚠️ Reparo também falhou",
    )

    if repaired.get("error"):
        return result  # both failed — surface the original, more specific error
    repaired["task_question"] = task_question
    repaired["repaired"] = True
    return repaired


def run_science_with_repair(
    silver_df: pd.DataFrame,
    task_question: str,
    progress_callback: ProgressCallback = _noop_progress,
    stage: str = "science",
    stage_log: "list[dict[str, Any]] | None" = None,
    run_id: str = "",
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
    locale: str = DEFAULT_LOCALE,
) -> ScienceResult:
    """Same auto-repair strategy as `run_gold_with_repair`, for Science sub-tasks.

    `run_id`/`llm_provider_override`/`llm_model_override`: Sprint 30/gap-closing
    (ADR-031 §5) — see `run_gold_analysis`'s identical parameters.

    `locale`: Sprint 25 (ADR-036) — see `run_gold_analysis`'s identical parameter.
    """
    result = run_science_analysis(
        silver_df,
        task_question,
        progress_callback,
        stage,
        stage_log,
        run_id,
        llm_provider_override,
        llm_model_override,
        locale,
    )
    if not result.get("error"):
        return result

    fallback_question = (
        f"Resuma de forma simples os principais números relacionados a: {task_question}"
    )
    repair_stage = f"{stage}:repair"
    progress_callback(repair_stage, f"🔧 Science — reparo automático: {task_question}")
    progress_callback(
        repair_stage, "A sub-análise falhou; tentando uma versão simplificada da pergunta..."
    )
    repaired = run_science_analysis(
        silver_df,
        fallback_question,
        progress_callback,
        repair_stage,
        stage_log,
        run_id,
        llm_provider_override,
        llm_model_override,
        locale,
    )
    progress_callback(
        repair_stage,
        "✅ Reparo automático funcionou" if not repaired.get("error") else "⚠️ Reparo também falhou",
    )

    if repaired.get("error"):
        return result
    repaired["task_question"] = task_question
    repaired["repaired"] = True
    return repaired


def run_analysis_tasks(
    silver_df: pd.DataFrame,
    business_question: str,
    progress_callback: ProgressCallback = _noop_progress,
    stage_log: "list[dict[str, Any]] | None" = None,
    run_id: str = "",
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
    locale: str = DEFAULT_LOCALE,
) -> tuple[list[GoldResult], list[ScienceResult], TokenUsage]:
    """Decompose the business question and run each sub-task through Gold or Science.

    A single Gold/Science call answering a multi-part question in one shot tends to
    cover only part of it — the LLM has to prioritize within one code generation. This
    runs the Planner first, then one Gold/Science call per sub-task (with a one-shot
    auto-repair fallback if a sub-task fails outright), so coverage of a "mega prompt"
    is a property of the loop instead of how well one call juggled it.

    `stage_log`: optional list (see `run_gold_analysis`/`_record_stage_call`) that
    every Analyst/Science call across every sub-task (and repair rerun) appends its
    ADR-007 latency entry to, in call order — left `None` (the default) keeps this
    function's own return signature unchanged for existing callers.

    `run_id`/`llm_provider_override`/`llm_model_override`: Sprint 30/gap-closing
    (ADR-031 §5) — forwarded to the Planner call and to every Gold/Science sub-task
    (and repair rerun) below. `None` (the default) — unchanged behavior.

    `locale`: Sprint 25 (ADR-036) — same forwarding, for the Planner/Analyst/Science
    output language. Defaults to `DEFAULT_LOCALE`.

    Returns (gold_results, science_results, planner_tokens).
    """
    progress_callback("planner", "🧭 Planner — decompondo a pergunta em sub-análises...")
    _log_llm_override_if_used("planner", run_id, llm_provider_override, llm_model_override)
    tasks, planner_tokens = plan_analysis_tasks(
        business_question, silver_df, llm_provider_override, llm_model_override, locale
    )
    progress_callback("planner", f"✅ {len(tasks)} sub-análise(s) planejada(s)")
    for t in tasks:
        progress_callback("planner", f"- ({t['type']}) {t['question']}")

    gold_results: list[GoldResult] = []
    science_results: list[ScienceResult] = []
    for i, task in enumerate(tasks):
        if task["type"] == "descriptive":
            gold_results.append(
                run_gold_with_repair(
                    silver_df,
                    task["question"],
                    progress_callback,
                    f"gold:{i}",
                    stage_log,
                    run_id,
                    llm_provider_override,
                    llm_model_override,
                    locale,
                )
            )
        else:
            science_results.append(
                run_science_with_repair(
                    silver_df,
                    task["question"],
                    progress_callback,
                    f"science:{i}",
                    stage_log,
                    run_id,
                    llm_provider_override,
                    llm_model_override,
                    locale,
                )
            )
    return gold_results, science_results, planner_tokens


def sum_run_tokens(
    gold_results: list[GoldResult],
    science_results: list[ScienceResult],
    advisor_result: AdvisorResult | dict[str, Any],
    planner_tokens: TokenUsage,
) -> TokenUsage:
    """Aggregate token usage across the Planner call, every Gold/Science sub-task, and
    the Advisor call, so the caller can show a single cost figure per run."""
    per_call = [g.get("tokens", {}) for g in gold_results]
    per_call += [s.get("tokens", {}) for s in science_results]
    per_call.append(advisor_result.get("tokens", {}))
    per_call.append(planner_tokens)

    total: TokenUsage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for tokens in per_call:
        t = tokens or {}
        total["input_tokens"] += t.get("input_tokens", 0)
        total["output_tokens"] += t.get("output_tokens", 0)
        total["total_tokens"] += t.get("total_tokens", 0)
    return total


def run_advisor_analysis(
    silver_df: pd.DataFrame,
    business_question: str,
    gold_results: list[GoldResult],
    science_results: list[ScienceResult],
    progress_callback: ProgressCallback = _noop_progress,
    run_id: str = "",
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
    locale: str = DEFAULT_LOCALE,
) -> AdvisorResult:
    """Synthesize Gold/Science results into prescriptive recommendations.

    `run_id`/`llm_provider_override`/`llm_model_override`: Sprint 30/gap-closing
    (ADR-031 §5) — see `run_gold_analysis`'s identical parameters.

    `locale`: Sprint 25 (ADR-036) — see `run_gold_analysis`'s identical parameter.
    """
    progress_callback("advisor", "🎯 Advisor — recomendações prescritivas...")
    progress_callback(
        "advisor", "🤖 **Advisor Agent** — Sintetizando análises e gerando recomendações..."
    )
    _log_llm_override_if_used("advisor", run_id, llm_provider_override, llm_model_override)
    t0 = time.time()
    result = run_advisor(
        silver_df,
        business_question,
        gold_results,
        science_results,
        llm_provider_override,
        llm_model_override,
        locale,
    )
    elapsed = round(time.time() - t0, 1)
    n = len(result.get("recommendations", []))

    if result["error"]:
        # See run_gold_analysis's comment above — same rationale.
        logger.warning(
            "run_advisor_analysis: advisor failed after %ss: %s", elapsed, result["error"]
        )
        progress_callback("advisor", f"⚠️ Advisor com aviso ({elapsed}s)")
    else:
        progress_callback("advisor", f"✅ {n} recomendações geradas em {elapsed}s")
    return result


def run_full_analysis(
    spec: str,
    business_question: str,
    run_dir: str,
    progress_callback: ProgressCallback = _noop_progress,
    tenant_id: str | None = None,
    saved_pipeline_id: str | None = None,
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
) -> AnalysisRunResult:
    """Run the full Silver -> Planner -> Gold/Science -> Advisor pipeline for one
    business question, persisting Silver + analysis results as a side effect.

    This is the orchestration previously inlined in `app.py`'s "Analisar dados"
    button handler. If Silver fails or produces an empty/missing DataFrame, Planner/
    Gold/Science/Advisor are skipped entirely and `advisor` comes back as `{}` (the
    legacy sentinel meaning "not run" — see `AnalysisRunResult`), matching the
    original short-circuit exactly.

    Args:
        tenant_id: Sprint A session-scoping stopgap forwarded to `save_run`/
            `save_analysis` — the browser session's UUID. Defaults to `None` for
            backward compatibility; `app.py` should always pass a real value.
        saved_pipeline_id: Sprint 17 (ADR-017) — forwarded to `save_run`/
            `save_analysis` so a scheduled fire can be grouped with the rest of
            its saved pipeline's run history. `None` for every avulso run.
            Sprint 14 (ADR-018)'s drift check is not decided here — it's the
            caller's job (`services/execution_queue.py`, once this function
            returns with the full `gold`/`science`/`advisor` results) — this
            function only needs to keep forwarding the id.
        llm_provider_override/llm_model_override: gap-closing fix (2026-08-25
            audit, Wave 4) — forwarded straight to `run_silver_pipeline`, which
            only actually uses them when `saved_pipeline_id` doesn't already
            resolve a real override from the DB (see that function's docstring).
    """
    silver_state = run_silver_pipeline(
        spec,
        run_dir,
        progress_callback,
        tenant_id=tenant_id,
        saved_pipeline_id=saved_pipeline_id,
        llm_provider_override=llm_provider_override,
        llm_model_override=llm_model_override,
    )
    silver_df = silver_state.get("transformed_data")
    # Sprint 30/gap-closing (ADR-031 §5) — reuse the override `run_silver_pipeline`
    # already resolved into `PipelineState` (via `get_saved_pipeline_llm_config`)
    # instead of a second DB round-trip; the analytical layer (Planner/Analyst/
    # Science/Advisor) runs outside the graph/`PipelineState`, so it's threaded
    # through as plain parameters from here on.
    llm_provider_override = silver_state.get("llm_provider_override")
    llm_model_override = silver_state.get("llm_model_override")
    # ADR-031 gap fix: the actual resolved model name this run's `get_llm(
    # provider=..., model=...)` calls used (`agents/analysis/*.py`, via
    # `llm_model_override` above) — threaded into `save_analysis` below so
    # `_write_analysis_row`'s cost tracking reflects the model that actually ran,
    # not the deployment-global `AI_ETL_LLM_MODEL` env var `get_model_name()`
    # reads. Mirrors `get_llm()`'s own resolution: an explicit override wins,
    # otherwise fall back to the global default.
    resolved_model_name = llm_model_override or get_model_name()
    # Sprint 25 (ADR-036) — same reuse rationale as the LLM override above: the
    # tenant's locale, already resolved into `PipelineState` by `run_silver_pipeline`
    # (via `audit/db.py::get_locale`), forwarded as a plain parameter from here on.
    locale = silver_state.get("locale", DEFAULT_LOCALE)

    gold_results: list[GoldResult] = []
    science_results: list[ScienceResult] = []
    advisor_result: AdvisorResult | dict[str, Any] = {}
    planner_tokens: TokenUsage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    question = business_question.strip()

    if isinstance(silver_df, pd.DataFrame) and not silver_df.empty:
        # ADR-007: every Analyst/Science sandbox call within this analysis run
        # (one per sub-task, plus any repair reruns) appends its latency here,
        # in call order, so save_stage_latencies below can assign an
        # incrementing `seq` per stage.
        analysis_stage_log: list[dict[str, Any]] = []
        gold_results, science_results, planner_tokens = run_analysis_tasks(
            silver_df,
            question,
            progress_callback,
            analysis_stage_log,
            silver_state["run_id"],
            llm_provider_override,
            llm_model_override,
            locale,
        )
        advisor_result = run_advisor_analysis(
            silver_df,
            question,
            gold_results,
            science_results,
            progress_callback,
            silver_state["run_id"],
            llm_provider_override,
            llm_model_override,
            locale,
        )
        save_analysis(
            silver_state["run_id"],
            gold_results,
            science_results,
            advisor_result,
            planner_tokens,
            log_dir=run_dir,
            tenant_id=tenant_id,
            business_question=question,
            saved_pipeline_id=saved_pipeline_id,
            model_name=resolved_model_name,
        )
        save_stage_latencies(
            silver_state["run_id"],
            "analysis",
            tenant_id,
            analysis_stage_log,
            log_dir=run_dir,
        )

    total_tokens = sum_run_tokens(gold_results, science_results, advisor_result, planner_tokens)

    return {
        "state": silver_state,
        "gold": gold_results,
        "science": science_results,
        "advisor": advisor_result,
        "question": question,
        "tokens": total_tokens,
    }


def _reload_awaiting_state(
    run_id: str, tenant_id: str, run_dir: str
) -> tuple[PipelineState, Optional[str]]:
    """Shared reload step for `resume_pending_load`/`reject_pending_load`
    (Sprint 27, ADR-028): validate the run exists, belongs to this tenant,
    and is actually `awaiting_approval`, then reconstruct its
    `PipelineState` via the already-existing `load_full_result` (see the
    ADR's Decision 1 for why this — not a LangGraph checkpointer — is the
    resume mechanism). Raises `ValueError` with an actionable message on any
    of those checks failing; callers (the `/runs` router) map that to a
    4xx HTTP response.

    Returns `(state, saved_pipeline_id)` — `saved_pipeline_id` is returned
    alongside `state`, not folded into it: `PipelineState` has no such field
    (it's a `runs`-table column, threaded as a parameter into
    `save_run`/`save_analysis`, never part of the graph state itself), and
    every node contract in this project builds a *new* state dict rather
    than smuggling extra keys onto an existing one.
    """
    meta = get_run_status_and_pipeline(run_id, tenant_id)
    if meta is None:
        raise ValueError(f"Run {run_id!r} not found.")
    if meta["status"] != "awaiting_approval":
        raise ValueError(
            f"Run {run_id!r} is not awaiting approval (current status: {meta['status']!r})."
        )

    reloaded = load_full_result(run_id, log_dir=run_dir, tenant_id=tenant_id)
    if reloaded is None:
        raise ValueError(f"Run {run_id!r} could not be reloaded from storage.")
    state = cast(PipelineState, reloaded["state"])
    if not isinstance(state.get("transformed_data"), pd.DataFrame):
        raise ValueError(f"Run {run_id!r} has no reloadable Silver data to act on.")
    return state, cast(Optional[str], meta["saved_pipeline_id"])


def resume_pending_load(run_id: str, tenant_id: str, run_dir: str) -> PipelineState:
    """Sprint 27 (ADR-028) — an operator approved a gated write: perform the
    real write now, by calling `loader_node` directly a second time (never
    the full graph — Orchestrator/Extractor/Transformer already ran and
    their LLM cost is sunk; re-running them could also regenerate a
    *different* transformation than the one just previewed and approved).

    Persists the result via `save_run`'s existing upsert (the `runs` row
    moves from `awaiting_approval` to `completed`/`failed` in place), keeps
    Sprint 15's health-snapshot cache in sync via a direct
    `record_pipeline_health` call, and — only once the write actually
    succeeds — marks the pipeline as having had its first approved write
    (`mark_pipeline_approved`), which is what lets a future write clear the
    approval gate via `approval_threshold_rows` instead of being gated every
    time (ADR-028 Decision 2).

    Raises `ValueError` if `run_id` doesn't exist, isn't owned by
    `tenant_id`, isn't currently `awaiting_approval`, or its Silver data
    can't be reloaded — the caller (the `/runs` router) maps each case to a
    4xx response.
    """
    state, saved_pipeline_id = _reload_awaiting_state(run_id, tenant_id, run_dir)

    granted_state: PipelineState = {**state, "approval_granted": True}
    result_state = loader_node(granted_state)

    save_run(
        result_state, log_dir=run_dir, tenant_id=tenant_id, saved_pipeline_id=saved_pipeline_id
    )

    if saved_pipeline_id is not None:
        final_status = result_state.get("status") or "failed"
        try:
            record_pipeline_health(saved_pipeline_id, final_status, result_state.get("error"))
            if final_status == "completed":
                mark_pipeline_approved(saved_pipeline_id, tenant_id)
        except Exception:  # nosec B110 — best-effort bookkeeping, never fails an
            pass  # already-persisted, already-written approval outcome.

    return result_state


def reject_pending_load(
    run_id: str, tenant_id: str, run_dir: str, reason: str = ""
) -> PipelineState:
    """Sprint 27 (ADR-028) — an operator declined a gated write: never calls
    the real write path at all. Marks the run `failed` with an explicit
    "rejected by operator" error (distinct from a real Loader error) and
    persists that via the same `save_run` upsert `resume_pending_load` uses.

    Raises `ValueError` under the same conditions as `resume_pending_load`
    (see `_reload_awaiting_state`).
    """
    state, saved_pipeline_id = _reload_awaiting_state(run_id, tenant_id, run_dir)

    error_message = f"Rejected by operator: {reason}" if reason else "Rejected by operator."
    new_log = log_action(state, "loader", "load_rejected", {"reason": reason})
    result_state: PipelineState = {
        **state,
        "status": "failed",
        "error": error_message,
        "load_preview": None,
        "audit_log": new_log,
    }

    save_run(
        result_state, log_dir=run_dir, tenant_id=tenant_id, saved_pipeline_id=saved_pipeline_id
    )

    if saved_pipeline_id is not None:
        try:
            record_pipeline_health(saved_pipeline_id, "failed", error_message)
        except Exception:  # nosec B110 — best-effort bookkeeping, never fails an
            pass  # already-persisted rejection.

    return result_state
