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


def test_initial_state_approval_gate_defaults() -> None:
    """Sprint 27 (ADR-028) — an avulso run's default state is never gated."""
    state = initial_state(spec="any spec", run_id="x")
    assert state["approval_policy"] is None
    assert state["approval_granted"] is False
    assert state["load_preview"] is None


def test_initial_state_accepts_approval_policy() -> None:
    policy = {"require_approval": True, "threshold_rows": None, "last_approved_at": None}
    state = initial_state(spec="any spec", run_id="x", approval_policy=policy)
    assert state["approval_policy"] == policy
