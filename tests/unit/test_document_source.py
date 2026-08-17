"""Unit tests for the document (PDF/DOCX) source connector (ADR-010)."""

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ai_etl.sources.document_source import (
    MAX_ATTEMPTS,
    _extract_text,
    _structure_text,
    load_document,
)

VALID_ROWS = [
    {"product": "A", "revenue": 100},
    {"product": "B", "revenue": 200},
]


def _mock_llm(responses: list[str]) -> MagicMock:
    llm = MagicMock()
    llm.invoke.side_effect = [MagicMock(content=r) for r in responses]
    return llm


@pytest.fixture
def mock_get_llm(mocker):
    return mocker.patch("ai_etl.sources.document_source.get_llm")


def test_extract_text_rejects_unsupported_extension() -> None:
    with pytest.raises(ValueError, match="Unsupported document type"):
        _extract_text("report.txt")


def test_extract_text_dispatches_pdf(mocker) -> None:
    mock_pdf = mocker.patch("ai_etl.sources.document_source._extract_pdf_text", return_value="x")
    _extract_text("report.pdf")
    mock_pdf.assert_called_once_with("report.pdf")


def test_extract_text_dispatches_docx(mocker) -> None:
    mock_docx = mocker.patch("ai_etl.sources.document_source._extract_docx_text", return_value="x")
    _extract_text("report.docx")
    mock_docx.assert_called_once_with("report.docx")


def test_structure_text_valid_json_returns_dataframe(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([json.dumps(VALID_ROWS)])
    df = _structure_text("some extracted document text")

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["product", "revenue"]
    assert len(df) == 2


def test_structure_text_retries_on_invalid_json(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm(["not json at all", json.dumps(VALID_ROWS)])
    df = _structure_text("some text")

    assert len(df) == 2


def test_structure_text_retries_on_non_array_json(mock_get_llm) -> None:
    """A JSON object (not an array) is syntactically valid JSON but the wrong
    shape — must be treated as a retry-able failure, not accepted as-is."""
    mock_get_llm.return_value = _mock_llm([json.dumps({"not": "a list"}), json.dumps(VALID_ROWS)])
    df = _structure_text("some text")

    assert len(df) == 2


def test_structure_text_exhausts_retries_and_raises(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm(["not json"] * MAX_ATTEMPTS)

    with pytest.raises(ValueError, match="Failed to extract structured data"):
        _structure_text("some text")

    assert mock_get_llm.return_value.invoke.call_count == MAX_ATTEMPTS


def test_load_document_composes_extract_and_structure(mocker) -> None:
    mocker.patch("ai_etl.sources.document_source._extract_text", return_value="raw text")
    mock_structure = mocker.patch(
        "ai_etl.sources.document_source._structure_text",
        return_value=pd.DataFrame(VALID_ROWS),
    )

    df = load_document("report.pdf")

    mock_structure.assert_called_once_with("raw text")
    assert len(df) == 2
