"""Unit tests for the Extractor Agent."""

import pandas as pd

from ai_etl.agents.extractor import _extract_schema, extractor_node
from ai_etl.core.state import initial_state


def _make_state(sources: list[dict]) -> dict:
    state = initial_state(spec="test", run_id="test-run")
    return {**state, "pipeline_plan": {"sources": sources}}


def test_csv_source_extracted(mocker) -> None:
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    mocker.patch("ai_etl.agents.extractor.load_csv", return_value=df)

    sources = [{"name": "orders", "type": "csv", "path": "data/orders.csv"}]
    result = extractor_node(_make_state(sources))

    assert "orders" in result["extracted_data"]
    assert result["extracted_data"]["orders"].equals(df)
    assert "orders" in result["source_schemas"]
    assert result["error"] is None


def test_document_source_extracted(mocker) -> None:
    df = pd.DataFrame({"product": ["A", "B"], "revenue": [100, 200]})
    mocker.patch("ai_etl.agents.extractor.load_document", return_value=df)

    sources = [{"name": "report", "type": "document", "path": "report.pdf"}]
    result = extractor_node(_make_state(sources))

    assert "report" in result["extracted_data"]
    assert result["extracted_data"]["report"].equals(df)
    assert result["error"] is None


def test_source_schema_has_required_fields(mocker) -> None:
    df = pd.DataFrame({"price": [10.0, None], "qty": [2, 3]})
    mocker.patch("ai_etl.agents.extractor.load_csv", return_value=df)

    sources = [{"name": "sales", "type": "csv", "path": "sales.csv"}]
    result = extractor_node(_make_state(sources))

    schema = result["source_schemas"]["sales"]
    assert "columns" in schema
    assert "dtypes" in schema
    assert "shape" in schema
    assert "sample" in schema
    assert "null_counts" in schema
    assert schema["null_counts"]["price"] == 1


def test_connector_failure_sets_error(mocker) -> None:
    mocker.patch("ai_etl.agents.extractor.load_csv", side_effect=FileNotFoundError("not found"))

    sources = [{"name": "missing", "type": "csv", "path": "missing.csv"}]
    result = extractor_node(_make_state(sources))

    assert result["error"] is not None
    assert "missing" in result["error"]
    assert result["status"] == "failed"


def test_upstream_error_short_circuits(mocker) -> None:
    mock_load = mocker.patch("ai_etl.agents.extractor.load_csv")
    state = _make_state([{"name": "x", "type": "csv", "path": "x.csv"}])
    state["error"] = "upstream failure"

    result = extractor_node(state)

    mock_load.assert_not_called()
    assert result["error"] == "upstream failure"


def test_unsupported_source_type_sets_error() -> None:
    sources = [{"name": "exotic", "type": "parquet", "path": "data.parquet"}]
    result = extractor_node(_make_state(sources))

    assert result["error"] is not None
    assert result["status"] == "failed"


def test_audit_log_added_on_success(mocker) -> None:
    df = pd.DataFrame({"col": [1]})
    mocker.patch("ai_etl.agents.extractor.load_csv", return_value=df)

    sources = [{"name": "s", "type": "csv", "path": "s.csv"}]
    result = extractor_node(_make_state(sources))

    assert any(e["action"] == "extraction_complete" for e in result["audit_log"])


# --- _extract_schema() direct tests ---


def test_extract_schema_shape_is_list() -> None:
    df = pd.DataFrame({"x": [1, 2, 3]})
    schema = _extract_schema(df)
    assert isinstance(schema["shape"], list)
    assert schema["shape"] == [3, 1]
