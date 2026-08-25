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
import pytest

from ai_etl.core.llm import get_model_name
from ai_etl.core.state import initial_state
from ai_etl.services import pipeline_service

_ZERO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


@pytest.fixture(autouse=True)
def _default_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sprint 25 (ADR-036): `run_silver_pipeline` calls `get_locale(tenant_id)` for
    every run with a real `tenant_id` — default it to a no-DB stub here so every
    existing test in this module (most of which pass `tenant_id="tenant-a"` without
    caring about locale) doesn't need a real `users` table. Tests that specifically
    exercise locale resolution override this with their own `monkeypatch.setattr`."""
    monkeypatch.setattr(pipeline_service, "get_locale", lambda tenant_id: "pt-BR")


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
    def __init__(
        self, chunks: list[dict[str, Any]], captured_states: list[Any] | None = None
    ) -> None:
        self._chunks = chunks
        self._captured_states = captured_states

    def stream(self, state: dict[str, Any]) -> Any:
        if self._captured_states is not None:
            self._captured_states.append(state)
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

    def fake_save_run(
        state: Any,
        log_dir: str,
        tenant_id: str | None = None,
        saved_pipeline_id: str | None = None,
    ) -> pathlib.Path:
        saved["state"] = state
        saved["log_dir"] = log_dir
        saved["tenant_id"] = tenant_id
        saved["saved_pipeline_id"] = saved_pipeline_id
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


def test_run_silver_pipeline_forwards_saved_pipeline_id_to_save_run(monkeypatch) -> None:
    """Sprint 14 (ADR-018): a scheduled fire's pipeline id must reach
    `save_run` so `runs.saved_pipeline_id` records the link."""
    chunks = [{"loader": {"status": "completed"}}]
    monkeypatch.setattr(pipeline_service, "build_graph", lambda: _FakeGraph(chunks))

    saved: dict[str, Any] = {}

    def fake_save_run(
        state: Any,
        log_dir: str,
        tenant_id: str | None = None,
        saved_pipeline_id: str | None = None,
    ) -> pathlib.Path:
        saved["saved_pipeline_id"] = saved_pipeline_id
        return pathlib.Path(log_dir) / "run.json"

    monkeypatch.setattr(pipeline_service, "save_run", fake_save_run)
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    pipeline_service.run_silver_pipeline("read file.csv", run_dir="runs", saved_pipeline_id="pl-1")

    assert saved["saved_pipeline_id"] == "pl-1"


def test_run_silver_pipeline_threads_quality_rules_for_scheduled_fire(monkeypatch) -> None:
    """Sprint 16 (ADR-023): a scheduled fire (both `saved_pipeline_id` and `tenant_id`
    set) resolves that pipeline's `quality_rules` and carries them into the initial
    `PipelineState`."""
    chunks = [{"loader": {"status": "completed"}}]
    captured_states: list[Any] = []
    monkeypatch.setattr(
        pipeline_service, "build_graph", lambda: _FakeGraph(chunks, captured_states)
    )
    monkeypatch.setattr(pipeline_service, "save_run", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    rules = [{"column": "amount", "operator": "gte", "value": 0, "severity": "error"}]
    monkeypatch.setattr(
        pipeline_service,
        "get_saved_pipeline",
        lambda pipeline_id, tenant_id: {"id": pipeline_id, "quality_rules": rules},
    )
    monkeypatch.setattr(
        pipeline_service, "get_saved_pipeline_llm_config", lambda pipeline_id, tenant_id: None
    )

    pipeline_service.run_silver_pipeline(
        "read file.csv", run_dir="runs", tenant_id="tenant-a", saved_pipeline_id="pl-1"
    )

    assert captured_states[0]["custom_quality_rules"] == rules


def test_run_silver_pipeline_avulso_run_gets_empty_quality_rules(monkeypatch) -> None:
    """An avulso run (no `saved_pipeline_id`) must never look up quality rules —
    `get_saved_pipeline` is not even called."""
    chunks = [{"loader": {"status": "completed"}}]
    captured_states: list[Any] = []
    monkeypatch.setattr(
        pipeline_service, "build_graph", lambda: _FakeGraph(chunks, captured_states)
    )
    monkeypatch.setattr(pipeline_service, "save_run", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    def _fail_if_called(pipeline_id: str, tenant_id: str) -> Any:
        raise AssertionError("get_saved_pipeline must not be called for an avulso run")

    monkeypatch.setattr(pipeline_service, "get_saved_pipeline", _fail_if_called)

    pipeline_service.run_silver_pipeline("read file.csv", run_dir="runs", tenant_id="tenant-a")

    assert captured_states[0]["custom_quality_rules"] == []


def test_run_silver_pipeline_resolves_llm_override_for_scheduled_fire(monkeypatch) -> None:
    """Sprint 30/gap-closing (ADR-031 §5) — a scheduled fire resolves its saved
    pipeline's llm_provider/llm_model override and carries it into the initial
    `PipelineState`, the same way quality_rules/approval_policy already do."""
    chunks = [{"loader": {"status": "completed"}}]
    captured_states: list[Any] = []
    monkeypatch.setattr(
        pipeline_service, "build_graph", lambda: _FakeGraph(chunks, captured_states)
    )
    monkeypatch.setattr(pipeline_service, "save_run", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)
    monkeypatch.setattr(
        pipeline_service,
        "get_saved_pipeline",
        lambda pipeline_id, tenant_id: {"id": pipeline_id, "quality_rules": []},
    )
    monkeypatch.setattr(
        pipeline_service,
        "get_saved_pipeline_llm_config",
        lambda pipeline_id, tenant_id: {
            "llm_provider": "anthropic",
            "llm_model": "claude-sonnet-5",
        },
    )

    pipeline_service.run_silver_pipeline(
        "read file.csv", run_dir="runs", tenant_id="tenant-a", saved_pipeline_id="pl-1"
    )

    assert captured_states[0]["llm_provider_override"] == "anthropic"
    assert captured_states[0]["llm_model_override"] == "claude-sonnet-5"


def test_run_silver_pipeline_resolves_locale_for_any_run_with_a_tenant_id(monkeypatch) -> None:
    """Sprint 25 (ADR-036) — unlike quality_rules/the LLM override above, locale is
    resolved for ANY run with a real `tenant_id`, avulso or scheduled — it does not
    require a `saved_pipeline_id` too."""
    chunks = [{"loader": {"status": "completed"}}]
    captured_states: list[Any] = []
    monkeypatch.setattr(
        pipeline_service, "build_graph", lambda: _FakeGraph(chunks, captured_states)
    )
    monkeypatch.setattr(pipeline_service, "save_run", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_service, "get_locale", lambda tenant_id: "en-US")

    pipeline_service.run_silver_pipeline("read file.csv", run_dir="runs", tenant_id="tenant-a")

    assert captured_states[0]["locale"] == "en-US"


def test_run_silver_pipeline_no_tenant_id_defaults_locale_without_a_db_call(monkeypatch) -> None:
    """An avulso run with no `tenant_id` at all (e.g. a script/test caller) must not
    call `get_locale` — there's no tenant to look one up for."""
    chunks = [{"loader": {"status": "completed"}}]
    captured_states: list[Any] = []
    monkeypatch.setattr(
        pipeline_service, "build_graph", lambda: _FakeGraph(chunks, captured_states)
    )
    monkeypatch.setattr(pipeline_service, "save_run", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    def _fail_if_called(tenant_id: str) -> Any:
        raise AssertionError("get_locale must not be called with no tenant_id")

    monkeypatch.setattr(pipeline_service, "get_locale", _fail_if_called)

    pipeline_service.run_silver_pipeline("read file.csv", run_dir="runs")

    assert captured_states[0]["locale"] == "pt-BR"


def test_run_silver_pipeline_avulso_run_gets_no_llm_override(monkeypatch) -> None:
    """An avulso run (no `saved_pipeline_id`) never looks up an LLM override —
    `get_saved_pipeline_llm_config` is not even called."""
    chunks = [{"loader": {"status": "completed"}}]
    captured_states: list[Any] = []
    monkeypatch.setattr(
        pipeline_service, "build_graph", lambda: _FakeGraph(chunks, captured_states)
    )
    monkeypatch.setattr(pipeline_service, "save_run", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    def _fail_if_called(pipeline_id: str, tenant_id: str) -> Any:
        raise AssertionError("get_saved_pipeline_llm_config must not be called for an avulso run")

    monkeypatch.setattr(pipeline_service, "get_saved_pipeline_llm_config", _fail_if_called)

    pipeline_service.run_silver_pipeline("read file.csv", run_dir="runs", tenant_id="tenant-a")

    assert captured_states[0]["llm_provider_override"] is None
    assert captured_states[0]["llm_model_override"] is None


def test_run_silver_pipeline_avulso_run_uses_caller_supplied_llm_override(monkeypatch) -> None:
    """Gap-closing fix (2026-08-25 audit, Wave 4) — an avulso run has no
    `saved_pipeline_id` to resolve a DB override from, but a caller (e.g.
    `POST /runs`' `ModelPicker` selection) can still supply one directly."""
    chunks = [{"loader": {"status": "completed"}}]
    captured_states: list[Any] = []
    monkeypatch.setattr(
        pipeline_service, "build_graph", lambda: _FakeGraph(chunks, captured_states)
    )
    monkeypatch.setattr(pipeline_service, "save_run", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    pipeline_service.run_silver_pipeline(
        "read file.csv",
        run_dir="runs",
        tenant_id="tenant-a",
        llm_provider_override="anthropic",
        llm_model_override="claude-sonnet-5",
    )

    assert captured_states[0]["llm_provider_override"] == "anthropic"
    assert captured_states[0]["llm_model_override"] == "claude-sonnet-5"


def test_run_silver_pipeline_saved_pipeline_override_wins_over_caller_override(
    monkeypatch,
) -> None:
    """A saved pipeline's own DB-configured override always wins over whatever
    a caller happened to pass — matching `run_silver_pipeline`'s docstring."""
    chunks = [{"loader": {"status": "completed"}}]
    captured_states: list[Any] = []
    monkeypatch.setattr(
        pipeline_service, "build_graph", lambda: _FakeGraph(chunks, captured_states)
    )
    monkeypatch.setattr(pipeline_service, "save_run", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)
    monkeypatch.setattr(
        pipeline_service,
        "get_saved_pipeline",
        lambda pipeline_id, tenant_id: {"id": pipeline_id, "quality_rules": []},
    )
    monkeypatch.setattr(
        pipeline_service,
        "get_saved_pipeline_llm_config",
        lambda pipeline_id, tenant_id: {
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
        },
    )

    pipeline_service.run_silver_pipeline(
        "read file.csv",
        run_dir="runs",
        tenant_id="tenant-a",
        saved_pipeline_id="pl-1",
        llm_provider_override="anthropic",
        llm_model_override="claude-sonnet-5",
    )

    assert captured_states[0]["llm_provider_override"] == "openai"
    assert captured_states[0]["llm_model_override"] == "gpt-4o-mini"


def test_run_silver_pipeline_reports_failure(monkeypatch) -> None:
    chunks = [{"orchestrator": {"status": "failed", "error": "Orchestrator failed: bad JSON"}}]
    monkeypatch.setattr(pipeline_service, "build_graph", lambda: _FakeGraph(chunks))
    monkeypatch.setattr(
        pipeline_service,
        "save_run",
        lambda state, log_dir, tenant_id=None, saved_pipeline_id=None: pathlib.Path("x"),
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
        pipeline_service, "run_analyst", lambda df, q, *a, **k: _gold_result(error=None, attempts=2)
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
        pipeline_service,
        "run_science",
        lambda df, q, *a, **k: _science_result(error=None, attempts=1),
    )

    events, _cb = _recorder()
    result = pipeline_service.run_science_analysis(
        pd.DataFrame({"a": [1]}), "pergunta", _cb, stage="science:0"
    )

    assert result["task_question"] == "pergunta"
    assert {stage for stage, _ in events} == {"science:0"}
    assert events[-1][1].startswith("✅ LinearRegression treinado")


def test_run_gold_analysis_attaches_sanity_check_on_success(monkeypatch) -> None:
    """Sprint 21 (ADR-026): a successful Gold sub-task gets a `sanity_check` key;
    a failed one does not (nothing to sanity-check)."""
    monkeypatch.setattr(
        pipeline_service, "run_analyst", lambda df, q, *a, **k: _gold_result(error=None)
    )

    result = pipeline_service.run_gold_analysis(pd.DataFrame({"a": [1]}), "pergunta")

    assert "sanity_check" in result
    assert result["sanity_check"]["severity"] in ("ok", "warning")


def test_run_gold_analysis_omits_sanity_check_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_service, "run_analyst", lambda df, q, *a, **k: _gold_result(error="boom")
    )

    result = pipeline_service.run_gold_analysis(pd.DataFrame({"a": [1]}), "pergunta")

    assert "sanity_check" not in result


def test_run_gold_analysis_surfaces_ressalva_for_a_fabricated_result(monkeypatch) -> None:
    """Definition of done: a deliberately wrong result (injected here) is flagged
    with a visible caveat, never silently accepted as trustworthy."""
    silver_df = pd.DataFrame({"amount": [10, 20, 30]})  # real total = 60
    fabricated = {
        "task_question": "",
        "gold_df": pd.DataFrame({"amount": [10_000]}),  # fabricated, way over the real total
        "fig": object(),
        "narrative": "Total de 10000",
        "code": "code",
        "attempts": 1,
        "error": None,
        "tokens": dict(_ZERO_TOKENS),
    }
    monkeypatch.setattr(pipeline_service, "run_analyst", lambda df, q, *a, **k: fabricated)

    events, _cb = _recorder()
    result = pipeline_service.run_gold_analysis(silver_df, "pergunta", _cb, stage="gold:0")

    assert result["sanity_check"]["severity"] == "warning"
    assert any("ressalva" in msg for _, msg in events)


def test_run_science_analysis_attaches_sanity_check_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_service, "run_science", lambda df, q, *a, **k: _science_result(error=None)
    )

    result = pipeline_service.run_science_analysis(pd.DataFrame({"a": [1]}), "pergunta")

    assert "sanity_check" in result


def test_run_science_analysis_omits_sanity_check_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_service, "run_science", lambda df, q, *a, **k: _science_result(error="boom")
    )

    result = pipeline_service.run_science_analysis(pd.DataFrame({"a": [1]}), "pergunta")

    assert "sanity_check" not in result


# ---------------------------------------------------------------------------
# run_gold_analysis / run_science_analysis — ADR-037 opt-in LLM review
# ---------------------------------------------------------------------------


def test_run_gold_analysis_skips_llm_review_when_disabled(monkeypatch) -> None:
    """Default (env var unset): the reviewer is never called at all."""

    def _fail_if_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("review_gold_result should not be called when disabled")

    monkeypatch.setattr(pipeline_service, "is_llm_review_enabled", lambda: False)
    monkeypatch.setattr(
        pipeline_service, "run_analyst", lambda df, q, *a, **k: _gold_result(error=None)
    )
    monkeypatch.setattr(pipeline_service, "review_gold_result", _fail_if_called)

    result = pipeline_service.run_gold_analysis(pd.DataFrame({"a": [1]}), "pergunta")

    assert result["tokens"] == _ZERO_TOKENS


def test_run_gold_analysis_appends_llm_review_entry_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_service, "is_llm_review_enabled", lambda: True)
    monkeypatch.setattr(
        pipeline_service, "run_analyst", lambda df, q, *a, **k: _gold_result(error=None)
    )
    review_entry = {
        "check": "llm_review",
        "severity": "warning",
        "detail": "answers a different question",
    }
    review_tokens = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    monkeypatch.setattr(
        pipeline_service, "review_gold_result", lambda *a, **k: ([review_entry], review_tokens)
    )

    result = pipeline_service.run_gold_analysis(pd.DataFrame({"a": [1]}), "pergunta")

    assert result["sanity_check"]["severity"] == "warning"
    assert review_entry in result["sanity_check"]["checks"]
    assert result["tokens"] == review_tokens  # base was all-zero, so the sum equals review_tokens


def test_run_gold_analysis_llm_review_returning_empty_still_counts_tokens(monkeypatch) -> None:
    """A review call that fails (see reviewer.py) still folds in its (zero) tokens
    and adds no entry — never crashes the sub-task."""
    monkeypatch.setattr(pipeline_service, "is_llm_review_enabled", lambda: True)
    monkeypatch.setattr(
        pipeline_service, "run_analyst", lambda df, q, *a, **k: _gold_result(error=None)
    )
    monkeypatch.setattr(
        pipeline_service, "review_gold_result", lambda *a, **k: ([], dict(_ZERO_TOKENS))
    )

    result = pipeline_service.run_gold_analysis(pd.DataFrame({"a": [1]}), "pergunta")

    assert result["sanity_check"]["severity"] == "ok"
    assert result["tokens"] == _ZERO_TOKENS


def test_run_science_analysis_appends_llm_review_entry_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_service, "is_llm_review_enabled", lambda: True)
    monkeypatch.setattr(
        pipeline_service, "run_science", lambda df, q, *a, **k: _science_result(error=None)
    )
    review_entry = {"check": "llm_review", "severity": "warning", "detail": "issue"}
    review_tokens = {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12}
    monkeypatch.setattr(
        pipeline_service,
        "review_science_result",
        lambda *a, **k: ([review_entry], review_tokens),
    )

    result = pipeline_service.run_science_analysis(pd.DataFrame({"a": [1]}), "pergunta")

    assert review_entry in result["sanity_check"]["checks"]
    assert result["tokens"] == review_tokens


def test_run_gold_analysis_appends_both_llm_review_entries_when_hedging_flagged(
    monkeypatch,
) -> None:
    """ADR-037 follow-up (2026-08-24 audit): a directional-hedge finding is a
    second, independent entry alongside the factual-consistency check, not a
    replacement for it."""
    monkeypatch.setattr(pipeline_service, "is_llm_review_enabled", lambda: True)
    monkeypatch.setattr(
        pipeline_service, "run_analyst", lambda df, q, *a, **k: _gold_result(error=None)
    )
    consistency_entry = {
        "check": "llm_review",
        "severity": "ok",
        "detail": "consistent with the data",
    }
    hedge_entry = {
        "check": "llm_review_hedge",
        "severity": "warning",
        "detail": "narrative never commits to a direction",
    }
    review_tokens = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    monkeypatch.setattr(
        pipeline_service,
        "review_gold_result",
        lambda *a, **k: ([consistency_entry, hedge_entry], review_tokens),
    )

    result = pipeline_service.run_gold_analysis(pd.DataFrame({"a": [1]}), "pergunta")

    assert consistency_entry in result["sanity_check"]["checks"]
    assert hedge_entry in result["sanity_check"]["checks"]
    assert result["sanity_check"]["severity"] == "warning"


# ---------------------------------------------------------------------------
# run_gold_with_repair / run_science_with_repair — auto-repair fallback
# ---------------------------------------------------------------------------


def test_run_gold_with_repair_returns_first_result_when_it_succeeds(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run_analyst(
        df: pd.DataFrame, question: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(question)
        return _gold_result(error=None)

    monkeypatch.setattr(pipeline_service, "run_analyst", fake_run_analyst)

    result = pipeline_service.run_gold_with_repair(pd.DataFrame({"a": [1]}), "pergunta original")

    assert calls == ["pergunta original"]  # no retry needed
    assert result.get("repaired") is not True
    assert result["error"] is None


def test_run_gold_with_repair_retries_with_simplified_question_on_failure(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run_analyst(
        df: pd.DataFrame, question: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
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
        pipeline_service, "run_analyst", lambda df, q, *a, **k: _gold_result(error=f"erro: {q}")
    )

    result = pipeline_service.run_gold_with_repair(pd.DataFrame({"a": [1]}), "pergunta original")

    assert result["error"] == "erro: pergunta original"
    assert "repaired" not in result


def test_run_science_with_repair_retries_on_failure(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run_science(
        df: pd.DataFrame, question: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
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
        lambda question, df, *a, **k: (
            [
                {"question": "quais os top produtos", "type": "descriptive"},
                {"question": "por que caiu", "type": "diagnostic_or_predictive"},
            ],
            {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        ),
    )
    monkeypatch.setattr(
        pipeline_service, "run_analyst", lambda df, q, *a, **k: _gold_result(error=None)
    )
    monkeypatch.setattr(
        pipeline_service, "run_science", lambda df, q, *a, **k: _science_result(error=None)
    )

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
        lambda spec, run_dir, progress_callback, tenant_id=None, saved_pipeline_id=None, llm_provider_override=None, llm_model_override=None: (
            (
                call_order.append("silver"),
                silver_state,
            )[1]
        ),
    )
    monkeypatch.setattr(
        pipeline_service,
        "plan_analysis_tasks",
        lambda question, df, *a, **k: (
            call_order.append("planner"),
            ([{"question": question, "type": "descriptive"}], dict(_ZERO_TOKENS)),
        )[1],
    )

    def fake_run_analyst(df: pd.DataFrame, q: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
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
        saved_pipeline_id: str | None = None,
        model_name: str | None = None,
    ) -> pathlib.Path:
        call_order.append("save_analysis")
        saved_analysis["run_id"] = run_id
        saved_analysis["log_dir"] = log_dir
        saved_analysis["tenant_id"] = tenant_id
        saved_analysis["model_name"] = model_name
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
    # No `llm_model_override` on `silver_state` here -> falls back to
    # get_model_name() (the global AI_ETL_LLM_MODEL/AI_ETL_LLM_PROVIDER default),
    # same as the pre-ADR-031-gap-fix behavior for a run with no per-pipeline
    # override.
    assert saved_analysis == {
        "run_id": "run-123",
        "log_dir": "runs",
        "tenant_id": None,
        "model_name": get_model_name(),
    }


def test_run_full_analysis_forwards_resolved_per_pipeline_model_to_save_analysis(
    monkeypatch,
) -> None:
    """ADR-031 gap fix regression guard: a run with a per-pipeline
    `llm_model_override` set on `PipelineState` (Sprint 30) must have that
    *actual* resolved model — not the global `AI_ETL_LLM_MODEL` default —
    forwarded to `save_analysis(..., model_name=...)`, so cost tracking prices
    the model that really ran. Before the fix, `save_analysis` had no
    `model_name` parameter at all and `_write_analysis_row` always re-derived
    the model from the global env var, silently mispricing every override run."""
    call_order: list[str] = []
    silver_state = _completed_silver_state("run-override-123", pd.DataFrame({"a": [1, 2]}))
    silver_state["llm_provider_override"] = "anthropic"
    silver_state["llm_model_override"] = "claude-opus-5"

    monkeypatch.setattr(
        pipeline_service,
        "run_silver_pipeline",
        lambda spec, run_dir, progress_callback, tenant_id=None, saved_pipeline_id=None, llm_provider_override=None, llm_model_override=None: (
            (
                call_order.append("silver"),
                silver_state,
            )[1]
        ),
    )
    monkeypatch.setattr(
        pipeline_service,
        "plan_analysis_tasks",
        lambda question, df, *a, **k: (
            call_order.append("planner"),
            ([{"question": question, "type": "descriptive"}], dict(_ZERO_TOKENS)),
        )[1],
    )

    def fake_run_analyst(df: pd.DataFrame, q: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
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
        saved_pipeline_id: str | None = None,
        model_name: str | None = None,
    ) -> pathlib.Path:
        call_order.append("save_analysis")
        saved_analysis["model_name"] = model_name
        return pathlib.Path(log_dir) / f"{run_id}_analysis.json"

    monkeypatch.setattr(pipeline_service, "run_analyst", fake_run_analyst)
    monkeypatch.setattr(pipeline_service, "run_advisor", fake_run_advisor)
    monkeypatch.setattr(pipeline_service, "save_analysis", fake_save_analysis)
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    pipeline_service.run_full_analysis("spec", "pergunta", run_dir="runs")

    assert saved_analysis["model_name"] == "claude-opus-5"


def test_run_full_analysis_forwards_caller_llm_override_to_silver(monkeypatch) -> None:
    """Gap-closing fix (2026-08-25 audit, Wave 4) — an avulso run's caller-
    supplied `llm_provider_override`/`llm_model_override` (e.g. `POST /runs`'
    `ModelPicker` selection) must reach `run_silver_pipeline`, which only had
    a DB-resolved override for saved-pipeline runs before this fix."""
    captured: dict[str, Any] = {}
    silver_state = _completed_silver_state("run-caller-override", pd.DataFrame({"a": [1]}))

    def fake_run_silver_pipeline(
        spec: str,
        run_dir: str,
        progress_callback: pipeline_service.ProgressCallback,
        tenant_id: str | None = None,
        saved_pipeline_id: str | None = None,
        llm_provider_override: str | None = None,
        llm_model_override: str | None = None,
    ) -> dict[str, Any]:
        captured["llm_provider_override"] = llm_provider_override
        captured["llm_model_override"] = llm_model_override
        return silver_state

    monkeypatch.setattr(pipeline_service, "run_silver_pipeline", fake_run_silver_pipeline)
    monkeypatch.setattr(
        pipeline_service,
        "plan_analysis_tasks",
        lambda question, df, *a, **k: (
            [{"question": question, "type": "descriptive"}],
            dict(_ZERO_TOKENS),
        ),
    )
    monkeypatch.setattr(pipeline_service, "run_analyst", lambda *a, **k: _gold_result(error=None))
    monkeypatch.setattr(pipeline_service, "run_advisor", lambda *a, **k: _advisor_result())
    monkeypatch.setattr(
        pipeline_service, "save_analysis", lambda *a, **k: pathlib.Path("runs/x_analysis.json")
    )
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    pipeline_service.run_full_analysis(
        "spec",
        "pergunta",
        run_dir="runs",
        llm_provider_override="anthropic",
        llm_model_override="claude-sonnet-5",
    )

    assert captured["llm_provider_override"] == "anthropic"
    assert captured["llm_model_override"] == "claude-sonnet-5"


def test_run_full_analysis_short_circuits_when_silver_produces_no_data(monkeypatch) -> None:
    failed_state = initial_state(spec="spec", run_id="run-999")
    failed_state["status"] = "failed"
    failed_state["error"] = "Transformer failed"
    failed_state["transformed_data"] = None

    monkeypatch.setattr(
        pipeline_service,
        "run_silver_pipeline",
        lambda spec, run_dir, progress_callback, tenant_id=None, saved_pipeline_id=None, llm_provider_override=None, llm_model_override=None: (
            failed_state
        ),
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
        saved_pipeline_id: str | None = None,
        llm_provider_override: str | None = None,
        llm_model_override: str | None = None,
    ) -> dict[str, Any]:
        progress_callback("silver", "⚡ Executando pipeline Silver...")
        progress_callback("silver", "✅ Silver concluído em 1.0s")
        return silver_state

    monkeypatch.setattr(pipeline_service, "run_silver_pipeline", fake_run_silver_pipeline)
    monkeypatch.setattr(
        pipeline_service,
        "plan_analysis_tasks",
        lambda question, df, *a, **k: (
            [{"question": question, "type": "descriptive"}],
            dict(_ZERO_TOKENS),
        ),
    )
    monkeypatch.setattr(
        pipeline_service, "run_analyst", lambda df, q, *a, **k: _gold_result(error=None)
    )
    monkeypatch.setattr(pipeline_service, "run_advisor", lambda *a, **k: _advisor_result())
    monkeypatch.setattr(pipeline_service, "save_analysis", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    events, _cb = _recorder()
    pipeline_service.run_full_analysis("spec", "pergunta", run_dir="runs", progress_callback=_cb)

    stages = {stage for stage, _ in events}
    assert stages == {"silver", "planner", "gold:0", "advisor"}


def test_run_silver_pipeline_resolves_approval_policy_for_scheduled_fire(monkeypatch) -> None:
    """Sprint 27 (ADR-028): a scheduled fire resolves its saved pipeline's
    write-approval policy and carries it into the initial `PipelineState`."""
    chunks = [{"loader": {"status": "completed"}}]
    captured_states: list[Any] = []
    monkeypatch.setattr(
        pipeline_service, "build_graph", lambda: _FakeGraph(chunks, captured_states)
    )
    monkeypatch.setattr(pipeline_service, "save_run", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)
    monkeypatch.setattr(
        pipeline_service,
        "get_saved_pipeline",
        lambda pipeline_id, tenant_id: {
            "id": pipeline_id,
            "quality_rules": [],
            "require_approval": True,
            "approval_threshold_rows": 500,
            "last_approved_at": None,
        },
    )
    monkeypatch.setattr(
        pipeline_service, "get_saved_pipeline_llm_config", lambda pipeline_id, tenant_id: None
    )

    pipeline_service.run_silver_pipeline(
        "read file.csv", run_dir="runs", tenant_id="tenant-a", saved_pipeline_id="pl-1"
    )

    assert captured_states[0]["approval_policy"] == {
        "require_approval": True,
        "threshold_rows": 500,
        "last_approved_at": None,
    }


def test_run_silver_pipeline_avulso_run_gets_no_approval_policy(monkeypatch) -> None:
    chunks = [{"loader": {"status": "completed"}}]
    captured_states: list[Any] = []
    monkeypatch.setattr(
        pipeline_service, "build_graph", lambda: _FakeGraph(chunks, captured_states)
    )
    monkeypatch.setattr(pipeline_service, "save_run", lambda *a, **k: pathlib.Path("x"))
    monkeypatch.setattr(pipeline_service, "save_stage_latencies", lambda *a, **k: None)

    pipeline_service.run_silver_pipeline("read file.csv", run_dir="runs", tenant_id="tenant-a")

    assert captured_states[0]["approval_policy"] is None


# ---------------------------------------------------------------------------
# resume_pending_load / reject_pending_load (Sprint 27, ADR-028)
# ---------------------------------------------------------------------------


def _awaiting_state() -> dict[str, Any]:
    state = initial_state(spec="test", run_id="run-1")
    return {
        **state,
        "pipeline_plan": {"destination": {"type": "csv", "path": "out.csv"}},
        "transformed_data": pd.DataFrame({"a": [1, 2, 3]}),
        "status": "awaiting_approval",
        "load_preview": {"would_write_rows": 3},
    }


def _patch_reload(monkeypatch, saved_pipeline_id: str | None = "pl-1") -> dict[str, Any]:
    state = _awaiting_state()
    monkeypatch.setattr(
        pipeline_service,
        "get_run_status_and_pipeline",
        lambda run_id, tenant_id: {
            "status": "awaiting_approval",
            "saved_pipeline_id": saved_pipeline_id,
        },
    )
    monkeypatch.setattr(
        pipeline_service, "load_full_result", lambda run_id, log_dir, tenant_id: {"state": state}
    )
    return state


def test_resume_pending_load_writes_and_marks_approved(monkeypatch) -> None:
    _patch_reload(monkeypatch)
    captured_loader_state: dict[str, Any] = {}

    def fake_loader_node(state: dict[str, Any]) -> dict[str, Any]:
        captured_loader_state.update(state)
        return {**state, "status": "completed", "load_result": {"rows_loaded": 3}, "error": None}

    monkeypatch.setattr(pipeline_service, "loader_node", fake_loader_node)

    saved: dict[str, Any] = {}
    monkeypatch.setattr(
        pipeline_service,
        "save_run",
        lambda state, log_dir, tenant_id=None, saved_pipeline_id=None: saved.update(
            state=state, saved_pipeline_id=saved_pipeline_id
        ),
    )
    health_calls: list[Any] = []
    monkeypatch.setattr(
        pipeline_service,
        "record_pipeline_health",
        lambda pid, status, error=None: health_calls.append((pid, status, error)),
    )
    approved_calls: list[Any] = []
    monkeypatch.setattr(
        pipeline_service,
        "mark_pipeline_approved",
        lambda pid, tenant_id: approved_calls.append((pid, tenant_id)),
    )

    result = pipeline_service.resume_pending_load("run-1", "tenant-a", run_dir="runs")

    assert captured_loader_state["approval_granted"] is True
    assert result["status"] == "completed"
    assert saved["saved_pipeline_id"] == "pl-1"
    assert health_calls == [("pl-1", "completed", None)]
    assert approved_calls == [("pl-1", "tenant-a")]


def test_resume_pending_load_write_failure_does_not_mark_approved(monkeypatch) -> None:
    _patch_reload(monkeypatch)
    monkeypatch.setattr(
        pipeline_service,
        "loader_node",
        lambda state: {**state, "status": "failed", "error": "disk full"},
    )
    monkeypatch.setattr(pipeline_service, "save_run", lambda *a, **k: None)
    health_calls: list[Any] = []
    monkeypatch.setattr(
        pipeline_service,
        "record_pipeline_health",
        lambda pid, status, error=None: health_calls.append((pid, status, error)),
    )
    approved_calls: list[Any] = []
    monkeypatch.setattr(
        pipeline_service,
        "mark_pipeline_approved",
        lambda pid, tenant_id: approved_calls.append((pid, tenant_id)),
    )

    result = pipeline_service.resume_pending_load("run-1", "tenant-a", run_dir="runs")

    assert result["status"] == "failed"
    assert health_calls == [("pl-1", "failed", "disk full")]
    assert approved_calls == []


def test_resume_pending_load_unknown_run_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_service, "get_run_status_and_pipeline", lambda run_id, tenant_id: None
    )
    with pytest.raises(ValueError, match="not found"):
        pipeline_service.resume_pending_load("missing", "tenant-a", run_dir="runs")


def test_resume_pending_load_wrong_status_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_service,
        "get_run_status_and_pipeline",
        lambda run_id, tenant_id: {"status": "completed", "saved_pipeline_id": None},
    )
    with pytest.raises(ValueError, match="not awaiting approval"):
        pipeline_service.resume_pending_load("run-1", "tenant-a", run_dir="runs")


def test_reject_pending_load_never_calls_loader_node(monkeypatch) -> None:
    _patch_reload(monkeypatch)
    monkeypatch.setattr(
        pipeline_service,
        "loader_node",
        lambda state: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    saved: dict[str, Any] = {}
    monkeypatch.setattr(
        pipeline_service,
        "save_run",
        lambda state, log_dir, tenant_id=None, saved_pipeline_id=None: saved.update(state=state),
    )
    monkeypatch.setattr(pipeline_service, "record_pipeline_health", lambda *a, **k: None)

    result = pipeline_service.reject_pending_load(
        "run-1", "tenant-a", run_dir="runs", reason="looks wrong"
    )

    assert result["status"] == "failed"
    assert "looks wrong" in result["error"]
    assert result["load_preview"] is None
    assert saved["state"]["status"] == "failed"


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
