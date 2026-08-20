"""Sprint 22 dirty-data corpus verification for `rest_source.py`'s JSON handling.

Unlike `csv_source.py`, no fix was needed here — investigated first, per this
sprint's scope, before assuming a rewrite was necessary. `load_rest`'s
existing `pd.json_normalize` call already handles both deeply nested JSON
(flattens into dotted column names) and irregular array/key shapes between
records (missing keys become `NaN`, no crash, no silent misalignment)
correctly. These tests pin that already-correct behavior against the real
corpus fixtures (`tests/fixtures/dirty_data/json_*.json`) rather than
leaving it unverified — same fixtures, network transport (`httpx.get`)
mocked with their real content, matching `test_rest_source_auth.py`'s
convention.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from ai_etl.sources.rest_source import load_rest

FIXTURES = Path(__file__).parent.parent / "fixtures" / "dirty_data"


def _mock_response(data: object) -> MagicMock:
    response = MagicMock()
    response.json.return_value = data
    response.raise_for_status.return_value = None
    return response


def _load_fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_load_rest_deeply_nested_json_flattens_correctly(mocker) -> None:
    data = _load_fixture("json_deeply_nested.json")
    mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=_mock_response(data))

    df = load_rest("https://api.example.com/pedidos")

    assert df.shape[0] == 2
    assert "cliente.nome" in df.columns
    assert "cliente.endereco.geo.lat" in df.columns
    assert df.loc[0, "cliente.nome"] == "Ana"
    assert df.loc[0, "cliente.endereco.cidade"] == "Recife"


def test_load_rest_irregular_arrays_fill_missing_with_nan(mocker) -> None:
    data = _load_fixture("json_irregular_arrays.json")
    mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=_mock_response(data))

    df = load_rest("https://api.example.com/itens")

    assert df.shape == (4, 4)
    assert list(df.columns) == ["id", "nome", "tags", "extra_field"]
    # Record 4 has no "tags" key at all — normalize fills it with NaN rather
    # than raising or silently dropping the row.
    assert df.loc[3, "tags"] is None or df["tags"].isna().iloc[3]
    # Record 1's "extra_field" (present on only one record) is NaN elsewhere.
    assert df["extra_field"].isna().sum() == 3
