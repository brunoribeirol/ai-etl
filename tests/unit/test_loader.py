"""Unit tests for the Loader Agent."""

import pandas as pd

from ai_etl.agents.pipeline.loader import _is_write_gated, loader_node
from ai_etl.core.state import initial_state


def _make_state(
    dest_type: str = "csv", dest_path: str = "output.csv", approval_policy: dict | None = None
) -> dict:
    state = initial_state(spec="test", run_id="test-run", approval_policy=approval_policy)
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    if dest_type == "csv":
        destination = {"type": dest_type, "path": dest_path}
    elif dest_type == "s3_parquet":
        destination = {"type": dest_type, "bucket": "my-bucket", "key": "warehouse/out.parquet"}
    elif dest_type == "mongodb":
        destination = {"type": dest_type, "database": "shop", "collection": "output"}
    else:
        destination = {"type": dest_type, "table": "public.output"}
    return {
        **state,
        "pipeline_plan": {"destination": destination},
        "transformed_data": df,
        "quality_report": {"severity": "ok", "checks": [], "summary": "0 checks"},
    }


def test_csv_load_succeeds(mocker) -> None:
    mock_save = mocker.patch(
        "ai_etl.agents.pipeline.loader.save_csv",
        return_value={"rows_loaded": 3, "destination": "output.csv"},
    )
    result = loader_node(_make_state())

    mock_save.assert_called_once()
    assert result["load_result"]["rows_loaded"] == 3
    assert result["load_result"]["destination"] == "output.csv"
    assert "timestamp" in result["load_result"]
    assert result["status"] == "completed"
    assert result["error"] is None


def test_audit_log_added_on_success(mocker) -> None:
    mocker.patch(
        "ai_etl.agents.pipeline.loader.save_csv",
        return_value={"rows_loaded": 3, "destination": "output.csv"},
    )
    result = loader_node(_make_state())

    assert any(e["action"] == "load_complete" for e in result["audit_log"])


def test_destination_failure_sets_error(mocker) -> None:
    mocker.patch("ai_etl.agents.pipeline.loader.save_csv", side_effect=OSError("disk full"))
    result = loader_node(_make_state())

    assert result["error"] is not None
    assert "disk full" in result["error"]
    assert result["status"] == "failed"


def test_s3_parquet_load_succeeds(mocker) -> None:
    mock_save = mocker.patch(
        "ai_etl.agents.pipeline.loader.save_s3_parquet",
        return_value={"rows_loaded": 3, "destination": "s3://my-bucket/warehouse/out.parquet"},
    )
    result = loader_node(_make_state(dest_type="s3_parquet"))

    mock_save.assert_called_once_with(mocker.ANY, "my-bucket", "warehouse/out.parquet")
    assert result["load_result"]["rows_loaded"] == 3
    assert result["load_result"]["destination"] == "s3://my-bucket/warehouse/out.parquet"
    assert "timestamp" in result["load_result"]
    assert result["status"] == "completed"
    assert result["error"] is None


def test_mysql_load_succeeds(mocker) -> None:
    mock_save = mocker.patch(
        "ai_etl.agents.pipeline.loader.save_mysql",
        return_value={"rows_loaded": 3, "destination": "public.output"},
    )
    result = loader_node(_make_state(dest_type="mysql"))

    mock_save.assert_called_once_with(mocker.ANY, "public.output")
    assert result["load_result"]["rows_loaded"] == 3
    assert result["status"] == "completed"
    assert result["error"] is None


def test_mongodb_load_succeeds(mocker) -> None:
    mock_save = mocker.patch(
        "ai_etl.agents.pipeline.loader.save_mongodb",
        return_value={"rows_loaded": 3, "destination": "shop.output"},
    )
    result = loader_node(_make_state(dest_type="mongodb"))

    mock_save.assert_called_once_with(mocker.ANY, "shop", "output")
    assert result["load_result"]["rows_loaded"] == 3
    assert result["status"] == "completed"
    assert result["error"] is None


def test_unsupported_destination_type_sets_error() -> None:
    state = _make_state()
    state["pipeline_plan"]["destination"] = {"type": "parquet", "path": "out.parquet"}
    result = loader_node(state)

    assert result["error"] is not None
    assert result["status"] == "failed"


def test_upstream_error_short_circuits(mocker) -> None:
    mock_save = mocker.patch("ai_etl.agents.pipeline.loader.save_csv")
    state = _make_state()
    state["error"] = "upstream failure"

    result = loader_node(state)

    mock_save.assert_not_called()
    assert result["error"] == "upstream failure"


# ---------------------------------------------------------------------------
# Sprint 27 (ADR-028) — write-approval gate
# ---------------------------------------------------------------------------


class TestIsWriteGated:
    def test_no_policy_never_gates(self) -> None:
        assert _is_write_gated(None, rows=1_000_000) is False

    def test_require_approval_false_never_gates(self) -> None:
        policy = {"require_approval": False, "threshold_rows": 0, "last_approved_at": None}
        assert _is_write_gated(policy, rows=1_000_000) is False

    def test_first_write_always_gated_regardless_of_threshold(self) -> None:
        policy = {"require_approval": True, "threshold_rows": 10_000, "last_approved_at": None}
        assert _is_write_gated(policy, rows=1) is True

    def test_no_threshold_after_approval_means_always_gated(self) -> None:
        policy = {
            "require_approval": True,
            "threshold_rows": None,
            "last_approved_at": "2026-08-01T00:00:00+00:00",
        }
        assert _is_write_gated(policy, rows=1) is True

    def test_below_threshold_after_approval_not_gated(self) -> None:
        policy = {
            "require_approval": True,
            "threshold_rows": 1000,
            "last_approved_at": "2026-08-01T00:00:00+00:00",
        }
        assert _is_write_gated(policy, rows=999) is False

    def test_at_or_above_threshold_after_approval_gated(self) -> None:
        policy = {
            "require_approval": True,
            "threshold_rows": 1000,
            "last_approved_at": "2026-08-01T00:00:00+00:00",
        }
        assert _is_write_gated(policy, rows=1000) is True


def test_gated_write_computes_preview_and_never_writes(mocker) -> None:
    mock_save = mocker.patch("ai_etl.agents.pipeline.loader.save_csv")
    mock_preview = mocker.patch(
        "ai_etl.agents.pipeline.loader.preview_csv",
        return_value={"destination_type": "csv", "would_write_rows": 3, "existing": None},
    )
    policy = {"require_approval": True, "threshold_rows": None, "last_approved_at": None}
    result = loader_node(_make_state(approval_policy=policy))

    mock_save.assert_not_called()
    mock_preview.assert_called_once()
    assert result["status"] == "awaiting_approval"
    assert result["load_result"] is None
    assert result["load_preview"]["would_write_rows"] == 3
    assert result["error"] is None
    assert any(e["action"] == "load_awaiting_approval" for e in result["audit_log"])


def test_approval_granted_bypasses_gate_and_writes(mocker) -> None:
    mock_save = mocker.patch(
        "ai_etl.agents.pipeline.loader.save_csv",
        return_value={"rows_loaded": 3, "destination": "output.csv"},
    )
    mock_preview = mocker.patch("ai_etl.agents.pipeline.loader.preview_csv")
    policy = {"require_approval": True, "threshold_rows": None, "last_approved_at": None}
    state = _make_state(approval_policy=policy)
    state["approval_granted"] = True

    result = loader_node(state)

    mock_preview.assert_not_called()
    mock_save.assert_called_once()
    assert result["status"] == "completed"
    assert result["load_result"]["rows_loaded"] == 3


def test_preview_failure_sets_error_without_writing(mocker) -> None:
    mock_save = mocker.patch("ai_etl.agents.pipeline.loader.save_csv")
    mocker.patch(
        "ai_etl.agents.pipeline.loader.preview_csv", side_effect=OSError("cannot stat path")
    )
    policy = {"require_approval": True, "threshold_rows": None, "last_approved_at": None}

    result = loader_node(_make_state(approval_policy=policy))

    mock_save.assert_not_called()
    assert result["status"] == "failed"
    assert "cannot stat path" in result["error"]
