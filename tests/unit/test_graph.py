"""Unit tests for graph routing logic."""

from langgraph.graph import END

from ai_etl.core.graph import build_graph, route_after_quality
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
