"""Tests for `ai_etl.services.pipeline_service` — the orchestration layer extracted
from `app.py`'s "Analisar dados" button handler.

These are characterization tests: they pin down the *existing* sequencing, retry,
persistence, and progress-reporting behavior (previously only exercisable by booting
Streamlit) so it can be refactored safely later without a UI in the loop.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pandas as pd

from ai_etl.core.state import initial_state
from ai_etl.services import pipeline_service

_ZERO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _recorder() -> tuple[list[tuple[str, str]], pipeline_service.ProgressCallback]:
    """A ProgressCallback that records every (stage, message) call it receives."""
    events: list[tuple[str, str]] = []

    def _callback(stage: str, message: str) -> None:
        events.append((stage, message))

    return events, _callback


def _gold_result(error: str | None = None, attempts: int = 1) -> dict[str, Any]:
    return {
        "task_question": "",
        "gold_df": pd.DataFrame({"x": [1]}) if error is None else pd.DataFrame(),
        "fig": object() if error is None else None,
        "narrative": "insight" if error is None else "",
        "code": "code",
        "attempts": attempts,
        "error": error,
        "tokens": dict(_ZERO_TOKENS),
    }


def _science_result(error: str | None = None, attempts: int = 1) -> dict[str, Any]:
    return {
        "task_question": "",
        "predictions_df": pd.DataFrame({"x": [1]}) if error is None else pd.DataFrame(),
        "fig": object() if error is None else None,
        "narrative": "insight" if error is None else "",
        "model_info": {"model_type": "LinearRegression"} if error is None else {},
        "code": "code",
        "attempts": attempts,
        "error": error,
        "tokens": dict(_ZERO_TOKENS),
    }


def _advisor_result(error: str | None = None) -> dict[str, Any]:
    return {
        "recommendations": []
        if error
        else [{"action": "a", "rationale": "r", "priority": "high", "expected_impact": "i"}],
        "summary": "resumo",
        "error": error,
        "tokens": dict(_ZERO_TOKENS),
    }


# ---------------------------------------------------------------------------
# run_silver_pipeline
# ---------------------------------------------------------------------------


class _FakeGraph:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks

    def stream(self, state: dict[str, Any]) -> Any:
        yield from self._chunks


def test_run_silver_pipeline_emits_progress_and_persists(monkeypatch) -> None:
    silver_df = pd.DataFrame({"a": [1, 2]})
    chunks = [
        {"orchestrator": {"pipeline_plan": {"sources": []}}},
        {
            "loader": {
                "status": "completed",
                "transformed_data": silver_df,
                "load_result": {"rows_loaded": 2},
            }
        },
    ]
    monkeypatch.setattr(pipeline_service, "build_graph", lambda: _FakeGraph(chunks))

    saved: dict[str, Any] = {}

    def fake_save_run(state: Any, log_dir: str, tenant_id: str | None = None) -> pathlib.Path:
        saved["state"] = state
        saved["log_dir"] = log_dir
        saved["tenant_id"] = tenant_id
        return pathlib.Path(log_dir) / "run.json"

    monkeypatch.setattr(pipeline_service, "save_run", fake_save_run)

    stage_latencies_calls: list[Any] = []
    monkeypatch.setattr(
        pipeline_service,
        "save_stage_latencies",
        lambda *args, **kwargs: stage_latencies_calls.append((args, kwargs)),
    )

    events, _cb = _recorder()
    result = pipeline_service.run_silver_pipeline(
        "read file.csv", run_dir="runs", progress_callback=_cb
    )

    assert result["status"] == "completed"
    assert result["transformed_data"] is silver_df
    assert "_total_time" in result and "_agent_timings" in result

    # save_run called exactly once, with the final merged state and the given run_dir
    assert saved["log_dir"] == "runs"
    assert saved["state"]["status"] == "completed"

    stages = {stage for stage, _ in events}
    assert stages == {"silver"}
    assert events[0][1] == "⚡ Executando pipeline Silver..."
    assert events[-1][1].startswith("✅ Silver concluído em")


def test_run_silver_pipeline_reports_failure(monkeypatch) -> None:
    chunks = [{"orchestrator": {"status": "failed", "error": "Orchestrator failed: bad JSON"}}]
    monkeypatch.setattr(pipeline_service, "build_graph", lambda: _FakeGraph(chunks))
    monkeypatch.setattr(
        pipeline_service, "save_run", lambda state, log_dir, tenant_id=None: pathlib.Path("x")
    )
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    events, _cb = _recorder()
    result = pipeline_service.run_silver_pipeline("bad spec", run_dir="runs", progress_callback=_cb)

    assert result["status"] == "failed"
    assert events[-1][1].startswith("❌ Silver falhou:")


# ---------------------------------------------------------------------------
# run_gold_analysis / run_science_analysis — single attempt, no retry
# ---------------------------------------------------------------------------


def test_run_gold_analysis_emits_progress_events(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_service, "run_analyst", lambda df, q: _gold_result(error=None, attempts=2)
    )

    events, _cb = _recorder()
    result = pipeline_service.run_gold_analysis(
        pd.DataFrame({"a": [1]}), "pergunta", _cb, stage="gold:0"
    )

    assert result["task_question"] == "pergunta"
    assert result["error"] is None
    assert {stage for stage, _ in events} == {"gold:0"}
    assert events[0][1] == "🏅 Gold — pergunta"
    assert events[-1][1].startswith("✅ Gold pronto")


def test_run_science_analysis_emits_progress_events(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_service, "run_science", lambda df, q: _science_result(error=None, attempts=1)
    )

    events, _cb = _recorder()
    result = pipeline_service.run_science_analysis(
        pd.DataFrame({"a": [1]}), "pergunta", _cb, stage="science:0"
    )

    assert result["task_question"] == "pergunta"
    assert {stage for stage, _ in events} == {"science:0"}
    assert events[-1][1].startswith("✅ LinearRegression treinado")


# ---------------------------------------------------------------------------
# run_gold_with_repair / run_science_with_repair — auto-repair fallback
# ---------------------------------------------------------------------------


def test_run_gold_with_repair_returns_first_result_when_it_succeeds(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run_analyst(df: pd.DataFrame, question: str) -> dict[str, Any]:
        calls.append(question)
        return _gold_result(error=None)

    monkeypatch.setattr(pipeline_service, "run_analyst", fake_run_analyst)

    result = pipeline_service.run_gold_with_repair(pd.DataFrame({"a": [1]}), "pergunta original")

    assert calls == ["pergunta original"]  # no retry needed
    assert result.get("repaired") is not True
    assert result["error"] is None


def test_run_gold_with_repair_retries_with_simplified_question_on_failure(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run_analyst(df: pd.DataFrame, question: str) -> dict[str, Any]:
        calls.append(question)
        if len(calls) == 1:
            return _gold_result(error="boom")
        return _gold_result(error=None)

    monkeypatch.setattr(pipeline_service, "run_analyst", fake_run_analyst)

    result = pipeline_service.run_gold_with_repair(pd.DataFrame({"a": [1]}), "pergunta original")

    assert len(calls) == 2
    assert calls[0] == "pergunta original"
    assert calls[1] != "pergunta original"  # fallback rewrites the question
    assert result["error"] is None
    assert result["repaired"] is True
    assert result["task_question"] == "pergunta original"  # restored on success


def test_run_gold_with_repair_surfaces_original_error_when_both_attempts_fail(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_service, "run_analyst", lambda df, q: _gold_result(error=f"erro: {q}")
    )

    result = pipeline_service.run_gold_with_repair(pd.DataFrame({"a": [1]}), "pergunta original")

    assert result["error"] == "erro: pergunta original"
    assert "repaired" not in result


def test_run_science_with_repair_retries_on_failure(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run_science(df: pd.DataFrame, question: str) -> dict[str, Any]:
        calls.append(question)
        if len(calls) == 1:
            return _science_result(error="boom")
        return _science_result(error=None)

    monkeypatch.setattr(pipeline_service, "run_science", fake_run_science)

    result = pipeline_service.run_science_with_repair(pd.DataFrame({"a": [1]}), "pergunta original")

    assert len(calls) == 2
    assert result["repaired"] is True
    assert result["error"] is None


# ---------------------------------------------------------------------------
# run_analysis_tasks — Planner + per-subtask routing
# ---------------------------------------------------------------------------


def test_run_analysis_tasks_routes_by_type_and_stages_each_subtask(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_service,
        "plan_analysis_tasks",
        lambda question, df: (
            [
                {"question": "quais os top produtos", "type": "descriptive"},
                {"question": "por que caiu", "type": "diagnostic_or_predictive"},
            ],
            {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        ),
    )
    monkeypatch.setattr(pipeline_service, "run_analyst", lambda df, q: _gold_result(error=None))
    monkeypatch.setattr(pipeline_service, "run_science", lambda df, q: _science_result(error=None))

    events, _cb = _recorder()
    gold_results, science_results, planner_tokens = pipeline_service.run_analysis_tasks(
        pd.DataFrame({"a": [1]}), "pergunta", _cb
    )

    assert len(gold_results) == 1
    assert len(science_results) == 1
    assert planner_tokens == {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}

    stages = {stage for stage, _ in events}
    assert "planner" in stages
    assert "gold:0" in stages
    assert "science:1" in stages


# ---------------------------------------------------------------------------
# run_advisor_analysis
# ---------------------------------------------------------------------------


def test_run_advisor_analysis_emits_progress_events(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_service, "run_advisor", lambda *a, **k: _advisor_result())

    events, _cb = _recorder()
    result = pipeline_service.run_advisor_analysis(
        pd.DataFrame({"a": [1]}), "pergunta", [], [], _cb
    )

    assert result["error"] is None
    assert {stage for stage, _ in events} == {"advisor"}
    assert events[-1][1].startswith("✅")


# ---------------------------------------------------------------------------
# sum_run_tokens (moved here from tests/unit/test_app.py::test_sum_run_tokens_*)
# ---------------------------------------------------------------------------


def test_sum_run_tokens_aggregates_across_all_calls() -> None:
    gold_results = [{"tokens": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}]
    science_results = [{"tokens": {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}}]
    advisor_result = {"tokens": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}}
    planner_tokens = {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4}

    total = pipeline_service.sum_run_tokens(
        gold_results, science_results, advisor_result, planner_tokens
    )

    assert total == {"input_tokens": 38, "output_tokens": 16, "total_tokens": 54}


def test_sum_run_tokens_handles_missing_tokens_key() -> None:
    total = pipeline_service.sum_run_tokens([{}], [{}], {}, {})

    assert total == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


# ---------------------------------------------------------------------------
# run_full_analysis — end-to-end sequencing, short-circuit, persistence timing
# ---------------------------------------------------------------------------


def _completed_silver_state(run_id: str, silver_df: pd.DataFrame) -> dict[str, Any]:
    state = initial_state(spec="spec", run_id=run_id)
    state["status"] = "completed"
    state["transformed_data"] = silver_df
    return state


def test_run_full_analysis_calls_stages_in_order_and_persists_analysis(monkeypatch) -> None:
    call_order: list[str] = []
    silver_state = _completed_silver_state("run-123", pd.DataFrame({"a": [1, 2]}))

    monkeypatch.setattr(
        pipeline_service,
        "run_silver_pipeline",
        lambda spec, run_dir, progress_callback, tenant_id=None: (
            call_order.append("silver"),
            silver_state,
        )[1],
    )
    monkeypatch.setattr(
        pipeline_service,
        "plan_analysis_tasks",
        lambda question, df: (
            call_order.append("planner"),
            ([{"question": question, "type": "descriptive"}], dict(_ZERO_TOKENS)),
        )[1],
    )

    def fake_run_analyst(df: pd.DataFrame, q: str) -> dict[str, Any]:
        call_order.append("gold")
        return _gold_result(error=None)

    def fake_run_advisor(*args: Any, **kwargs: Any) -> dict[str, Any]:
        call_order.append("advisor")
        return _advisor_result()

    saved_analysis: dict[str, Any] = {}

    def fake_save_analysis(
        run_id: str,
        gold: Any,
        science: Any,
        advisor: Any,
        planner_tokens: Any,
        log_dir: str,
        tenant_id: str | None = None,
        business_question: str = "",
    ) -> pathlib.Path:
        call_order.append("save_analysis")
        saved_analysis["run_id"] = run_id
        saved_analysis["log_dir"] = log_dir
        saved_analysis["tenant_id"] = tenant_id
        return pathlib.Path(log_dir) / f"{run_id}_analysis.json"

    monkeypatch.setattr(pipeline_service, "run_analyst", fake_run_analyst)
    monkeypatch.setattr(pipeline_service, "run_advisor", fake_run_advisor)
    monkeypatch.setattr(pipeline_service, "save_analysis", fake_save_analysis)
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    result = pipeline_service.run_full_analysis("spec", "  pergunta de negócio  ", run_dir="runs")

    assert call_order == ["silver", "planner", "gold", "advisor", "save_analysis"]
    assert result["question"] == "pergunta de negócio"  # stripped
    assert result["state"] is silver_state
    assert len(result["gold"]) == 1
    assert result["advisor"]["error"] is None
    assert saved_analysis == {"run_id": "run-123", "log_dir": "runs", "tenant_id": None}


def test_run_full_analysis_short_circuits_when_silver_produces_no_data(monkeypatch) -> None:
    failed_state = initial_state(spec="spec", run_id="run-999")
    failed_state["status"] = "failed"
    failed_state["error"] = "Transformer failed"
    failed_state["transformed_data"] = None

    monkeypatch.setattr(
        pipeline_service,
        "run_silver_pipeline",
        lambda spec, run_dir, progress_callback, tenant_id=None: failed_state,
    )

    downstream_called = {"planner": False, "advisor": False, "save_analysis": False}
    monkeypatch.setattr(
        pipeline_service,
        "plan_analysis_tasks",
        lambda *a, **k: downstream_called.__setitem__("planner", True),
    )
    monkeypatch.setattr(
        pipeline_service,
        "run_advisor",
        lambda *a, **k: downstream_called.__setitem__("advisor", True),
    )
    monkeypatch.setattr(
        pipeline_service,
        "save_analysis",
        lambda *a, **k: downstream_called.__setitem__("save_analysis", True),
    )

    result = pipeline_service.run_full_analysis("spec", "pergunta", run_dir="runs")

    assert downstream_called == {"planner": False, "advisor": False, "save_analysis": False}
    assert result["gold"] == []
    assert result["science"] == []
    assert result["advisor"] == {}  # legacy "not run" sentinel — see AnalysisRunResult docstring
    assert result["state"]["status"] == "failed"
    assert result["tokens"] == _ZERO_TOKENS


def test_run_full_analysis_forwards_progress_callback_to_every_stage(monkeypatch) -> None:
    silver_state = _completed_silver_state("run-1", pd.DataFrame({"a": [1]}))

    def fake_run_silver_pipeline(
        spec: str,
        run_dir: str,
        progress_callback: pipeline_service.ProgressCallback,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        progress_callback("silver", "⚡ Executando pipeline Silver...")
        progress_callback("silver", "✅ Silver concluído em 1.0s")
        return silver_state

    monkeypatch.setattr(pipeline_service, "run_silver_pipeline", fake_run_silver_pipeline)
    monkeypatch.setattr(
        pipeline_service,
        "plan_analysis_tasks",
        lambda question, df: ([{"question": question, "type": "descriptive"}], dict(_ZERO_TOKENS)),
    )
    monkeypatch.setattr(pipeline_service, "run_analyst", lambda df, q: _gold_result(error=None))
    monkeypatch.setattr(pipeline_service, "run_advisor", lambda *a, **k: _advisor_result())
    monkeypatch.setattr(pipeline_service, "save_analysis", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    events, _cb = _recorder()
    pipeline_service.run_full_analysis("spec", "pergunta", run_dir="runs", progress_callback=_cb)

    stages = {stage for stage, _ in events}
    assert stages == {"silver", "planner", "gold:0", "advisor"}


# ---------------------------------------------------------------------------
# Architectural guard — services/ must stay presentation-agnostic
# ---------------------------------------------------------------------------


def test_services_package_does_not_import_streamlit() -> None:
    services_dir = pathlib.Path(__file__).resolve().parents[2] / "src" / "ai_etl" / "services"
    offenders = []
    for path in sorted(services_dir.rglob("*.py")):
        text = path.read_text()
        if "import streamlit" in text or "from streamlit" in text:
            offenders.append(str(path))

    assert offenders == [], f"services/ must not depend on Streamlit, found in: {offenders}"
