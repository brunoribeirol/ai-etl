"""Unit tests for the Loader Agent."""

import pandas as pd

from ai_etl.agents.loader import loader_node
from ai_etl.core.state import initial_state


def _make_state(dest_type: str = "csv", dest_path: str = "output.csv") -> dict:
    state = initial_state(spec="test", run_id="test-run")
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    if dest_type == "csv":
        destination = {"type": dest_type, "path": dest_path}
    elif dest_type == "s3_parquet":
        destination = {"type": dest_type, "bucket": "my-bucket", "key": "warehouse/out.parquet"}
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
        "ai_etl.agents.loader.save_csv",
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
        "ai_etl.agents.loader.save_csv",
        return_value={"rows_loaded": 3, "destination": "output.csv"},
    )
    result = loader_node(_make_state())

    assert any(e["action"] == "load_complete" for e in result["audit_log"])


def test_destination_failure_sets_error(mocker) -> None:
    mocker.patch("ai_etl.agents.loader.save_csv", side_effect=OSError("disk full"))
    result = loader_node(_make_state())

    assert result["error"] is not None
    assert "disk full" in result["error"]
    assert result["status"] == "failed"


def test_s3_parquet_load_succeeds(mocker) -> None:
    mock_save = mocker.patch(
        "ai_etl.agents.loader.save_s3_parquet",
        return_value={"rows_loaded": 3, "destination": "s3://my-bucket/warehouse/out.parquet"},
    )
    result = loader_node(_make_state(dest_type="s3_parquet"))

    mock_save.assert_called_once_with(mocker.ANY, "my-bucket", "warehouse/out.parquet")
    assert result["load_result"]["rows_loaded"] == 3
    assert result["load_result"]["destination"] == "s3://my-bucket/warehouse/out.parquet"
    assert "timestamp" in result["load_result"]
    assert result["status"] == "completed"
    assert result["error"] is None


def test_unsupported_destination_type_sets_error() -> None:
    state = _make_state()
    state["pipeline_plan"]["destination"] = {"type": "parquet", "path": "out.parquet"}
    result = loader_node(state)

    assert result["error"] is not None
    assert result["status"] == "failed"


def test_upstream_error_short_circuits(mocker) -> None:
    mock_save = mocker.patch("ai_etl.agents.loader.save_csv")
    state = _make_state()
    state["error"] = "upstream failure"

    result = loader_node(state)

    mock_save.assert_not_called()
    assert result["error"] == "upstream failure"
