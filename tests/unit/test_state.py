"""Unit tests for PipelineState and initial_state."""

from ai_etl.core.state import initial_state


def test_initial_state_has_required_fields() -> None:
    state = initial_state(spec="load sales.csv", run_id="test-run-1")
    assert state["spec"] == "load sales.csv"
    assert state["run_id"] == "test-run-1"
    assert state["status"] == "running"
    assert state["audit_log"] == []
    assert state["error"] is None
    assert state["transformed_data"] is None


def test_initial_state_pipeline_plan_is_empty() -> None:
    state = initial_state(spec="any spec", run_id="x")
    assert state["pipeline_plan"] == {}
    assert state["extracted_data"] == {}
    assert state["source_schemas"] == {}
