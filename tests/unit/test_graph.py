"""Unit tests for graph routing logic and the `_timed()` latency wrapper."""

import time

from langgraph.graph import END

from ai_etl.core.graph import _timed, build_graph, route_after_quality
from ai_etl.core.state import initial_state


def _make_state(severity: str) -> dict:
    state = initial_state(spec="test", run_id="test")
    return {**state, "quality_report": {"severity": severity, "checks": [], "summary": ""}}


def test_route_ok_goes_to_loader() -> None:
    assert route_after_quality(_make_state("ok")) == "loader"


def test_route_warning_goes_to_loader() -> None:
    assert route_after_quality(_make_state("warning")) == "loader"


def test_route_error_goes_to_end() -> None:
    assert route_after_quality(_make_state("error")) == END


def test_route_missing_quality_report_defaults_to_loader() -> None:
    state = initial_state(spec="test", run_id="test")
    assert route_after_quality(state) == "loader"


def test_build_graph_returns_compiled_graph() -> None:
    graph = build_graph()
    assert graph is not None


# ---------------------------------------------------------------------------
# _timed() — ADR-007 per-stage latency instrumentation wrapper
# ---------------------------------------------------------------------------


def test_timed_wrapper_populates_stage_durations_with_plausible_value() -> None:
    def _slow_node(state: dict) -> dict:
        time.sleep(0.01)
        return {**state, "ran": True}

    wrapped = _timed("extractor", _slow_node)
    state = initial_state(spec="test", run_id="test")
    result = wrapped(state)

    assert result["ran"] is True
    assert "extractor" in result["stage_durations"]
    # Non-zero for anything doing real work (the node slept 10ms), non-negative,
    # and well below a sane upper bound — a regression that made this always 0
    # (or negative, e.g. a start/end swap) should fail here.
    assert result["stage_durations"]["extractor"] > 0
    assert result["stage_durations"]["extractor"] < 5


def test_timed_wrapper_merges_into_existing_stage_durations() -> None:
    def _node(state: dict) -> dict:
        return {**state}

    wrapped = _timed("transformer", _node)
    state = {**initial_state(spec="test", run_id="test"), "stage_durations": {"extractor": 0.1}}
    result = wrapped(state)

    assert set(result["stage_durations"].keys()) == {"extractor", "transformer"}
    assert result["stage_durations"]["extractor"] == 0.1
    assert result["stage_durations"]["transformer"] >= 0


def test_timed_wrapper_accumulates_on_repeated_calls_for_same_stage_name() -> None:
    """Per `_timed()`'s docstring, `durations[name] = durations.get(name, 0.0) +
    elapsed` — a second call for the same stage name adds to, rather than
    overwrites, the existing recorded duration."""

    def _node(state: dict) -> dict:
        return {**state}

    wrapped = _timed("extractor", _node)
    state = {**initial_state(spec="test", run_id="test"), "stage_durations": {"extractor": 1.0}}
    result = wrapped(state)

    assert result["stage_durations"]["extractor"] >= 1.0


def test_timed_wrapper_works_when_node_returns_partial_update() -> None:
    """A LangGraph node may return only the keys it changed, not a full state
    copy — `_timed()` must merge `stage_durations` on top of that partial dict
    without requiring the rest of the state to be present in its return value."""

    def _partial_node(state: dict) -> dict:
        return {"status": "completed"}

    wrapped = _timed("loader", _partial_node)
    state = initial_state(spec="test", run_id="test")
    result = wrapped(state)

    assert result["status"] == "completed"
    assert "loader" in result["stage_durations"]
