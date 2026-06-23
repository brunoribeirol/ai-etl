"""Unit tests for source and destination connectors."""

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ai_etl.destinations.csv_dest import save_csv
from ai_etl.sources.csv_source import load_csv
from ai_etl.sources.rest_source import load_rest

# --- csv_dest ---


def test_save_csv_writes_file(tmp_path: Path) -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    out = str(tmp_path / "output.csv")
    result = save_csv(df, out)

    assert Path(out).exists()
    loaded = pd.read_csv(out)
    assert list(loaded.columns) == ["a", "b"]
    assert len(loaded) == 3
    assert result["rows_loaded"] == 3
    assert result["destination"] == out


def test_save_csv_creates_parent_dirs(tmp_path: Path) -> None:
    df = pd.DataFrame({"x": [1]})
    out = str(tmp_path / "nested" / "dir" / "output.csv")
    save_csv(df, out)
    assert Path(out).exists()


# --- csv_source ---


def test_load_csv_reads_file(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("col1,col2\n1,a\n2,b\n")
    df = load_csv(str(csv_path))
    assert list(df.columns) == ["col1", "col2"]
    assert len(df) == 2


def test_load_csv_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_csv(str(tmp_path / "nonexistent.csv"))


# --- rest_source ---


def test_load_rest_list_response(mocker) -> None:
    data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    mock_response = MagicMock()
    mock_response.json.return_value = data
    mock_response.raise_for_status = MagicMock()
    mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=mock_response)

    df = load_rest("http://example.com/api/users")
    assert len(df) == 2
    assert "name" in df.columns


def test_load_rest_dict_with_data_key(mocker) -> None:
    data = {"data": [{"id": 1}, {"id": 2}], "total": 2}
    mock_response = MagicMock()
    mock_response.json.return_value = data
    mock_response.raise_for_status = MagicMock()
    mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=mock_response)

    df = load_rest("http://example.com/api")
    assert len(df) == 2


def test_load_rest_single_dict_response(mocker) -> None:
    data = {"status": "ok", "count": 42}
    mock_response = MagicMock()
    mock_response.json.return_value = data
    mock_response.raise_for_status = MagicMock()
    mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=mock_response)

    df = load_rest("http://example.com/api/status")
    assert len(df) == 1
    assert "status" in df.columns


def test_load_rest_time_series_nested_dict(mocker) -> None:
    """Open-Meteo pattern: {"daily": {"time": [...], "temperature_2m_max": [...]}}"""
    data = {
        "latitude": -8.05,
        "daily": {
            "time": ["2026-06-23", "2026-06-24"],
            "temperature_2m_max": [27.0, 27.2],
        },
    }
    mock_response = MagicMock()
    mock_response.json.return_value = data
    mock_response.raise_for_status = MagicMock()
    mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=mock_response)

    df = load_rest("http://api.open-meteo.com/v1/forecast")
    assert len(df) == 2
    assert "time" in df.columns
    assert "temperature_2m_max" in df.columns
    assert df["temperature_2m_max"].iloc[0] == 27.0


def test_load_rest_unexpected_type_raises(mocker) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = "unexpected string"
    mock_response.raise_for_status = MagicMock()
    mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=mock_response)

    with pytest.raises(ValueError, match="Unexpected JSON structure"):
        load_rest("http://example.com/api")
