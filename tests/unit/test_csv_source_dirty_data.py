"""Unit tests for csv_source.py's Sprint 22 dirty-data hardening.

Each test exercises `load_csv` against a real fixture in
`tests/fixtures/dirty_data/` (versioned in the repo, not gitignored — small
files, unlike the Sprint 12 synthetic scale benchmark). See
`src/ai_etl/sources/csv_source.py`'s module docstring for the three failure
classes found and fixed, and this file's own module docstring in each test
for what specifically was broken before the fix.
"""

from pathlib import Path

import pandas as pd
import pytest

from ai_etl.sources.csv_source import (
    _LARGE_FILE_ROW_THRESHOLD,
    _VALIDATION_SAMPLE_ROWS,
    _decode_bytes,
    _detect_delimiter,
    _validate_row_lengths,
    load_csv,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "dirty_data"


# --- Encoding ---
# Before the fix: `pd.read_csv`'s default `encoding="utf-8"` raised a raw
# `UnicodeDecodeError` for all three of these — confirmed by direct
# reproduction against the fixtures before writing `_decode_bytes`.


def test_load_csv_latin1_encoding() -> None:
    df = load_csv(str(FIXTURES / "csv_latin1_encoding.csv"))
    assert df.shape == (3, 3)
    assert df.loc[0, "nome"] == "José da Silva"
    assert df.loc[1, "cidade"] == "Brasília"


def test_load_csv_windows1252_encoding() -> None:
    df = load_csv(str(FIXTURES / "csv_windows1252_encoding.csv"))
    assert df.shape == (2, 3)
    assert "Premium" in df.loc[0, "produto"]


def test_load_csv_mixed_encoding_within_file() -> None:
    df = load_csv(str(FIXTURES / "csv_mixed_encoding.csv"))
    assert df.shape == (2, 3)
    assert df.loc[1, "nome"] == "José Antônio"


def test_decode_bytes_unicode_error_with_no_confident_candidate_is_actionable() -> None:
    # Bytes chosen to defeat UTF-8 decoding without giving charset_normalizer
    # anything plausible to detect either (a lone invalid continuation byte
    # with no surrounding text statistics to lean on).
    raw = b"\xff\xfe\x00\x01\x02"
    with pytest.raises(ValueError, match="not valid UTF-8"):
        _decode_bytes(raw, "broken.csv")


# --- Delimiter ---
# Before the fix: both of these parsed "successfully" under the default
# `sep=","` into a single column containing the whole raw line — silently
# wrong, not a crash. Confirmed by direct reproduction.


def test_load_csv_semicolon_delimiter() -> None:
    df = load_csv(str(FIXTURES / "csv_semicolon_delimiter.csv"))
    assert list(df.columns) == ["nome", "cidade", "valor"]
    assert df.shape == (2, 3)


def test_load_csv_tab_delimiter() -> None:
    df = load_csv(str(FIXTURES / "csv_tab_delimiter.csv"))
    assert list(df.columns) == ["nome", "cidade", "valor"]
    assert df.shape == (2, 3)


def test_detect_delimiter_falls_back_to_comma_when_ambiguous() -> None:
    assert _detect_delimiter("just one column\nno delimiters here\n") == ","


# --- Malformed quoting / stray delimiter ---
# Before the fix: pandas' C engine silently accepted the mismatched row
# count and returned a DataFrame with shifted, wrong values (confirmed —
# `nome` lost its prefix, `cidade` held a fragment of the previous field)
# instead of raising anything at all. This is the case the sprint's
# Definition of Done calls out as strictly worse than a crash.


def test_load_csv_malformed_quoting_raises_actionable_error() -> None:
    with pytest.raises(ValueError, match="Malformed CSV"):
        load_csv(str(FIXTURES / "csv_ambiguous_delimiter.csv"))


def test_validate_row_lengths_accepts_well_quoted_commas() -> None:
    text = 'nome,cidade,valor\nAna,"Recife, PE",10.5\n'
    _validate_row_lengths(text, ",", "ok.csv")  # must not raise


def test_validate_row_lengths_rejects_field_count_mismatch() -> None:
    text = "a,b,c\n1,2,3,4\n"
    with pytest.raises(ValueError, match="expected 3 fields"):
        _validate_row_lengths(text, ",", "bad.csv")


def test_validate_row_lengths_noop_on_empty_file() -> None:
    _validate_row_lengths("", ",", "empty.csv")  # must not raise


def _large_well_formed_csv(n_rows: int) -> str:
    lines = ["a,b,c"] + [f"{i},{i},{i}" for i in range(n_rows)]
    return "\n".join(lines) + "\n"


def test_validate_row_lengths_large_file_catches_mismatch_within_sample() -> None:
    """Sprint 22 code-review fix: above the row threshold, only a bounded
    leading sample is validated (not a second full-file pass) — a malformed
    row inside that sample must still be caught."""
    lines = ["a,b,c"] + [f"{i},{i},{i}" for i in range(_LARGE_FILE_ROW_THRESHOLD + 1000)]
    lines[10] = "1,2,3,4"  # well within _VALIDATION_SAMPLE_ROWS
    text = "\n".join(lines) + "\n"
    with pytest.raises(ValueError, match="expected 3 fields"):
        _validate_row_lengths(text, ",", "large_bad_early.csv")


def test_validate_row_lengths_large_file_skips_rows_past_sample_boundary() -> None:
    """Documented, accepted trade-off: a malformed row past the sample
    boundary in a very large file is not guaranteed to be caught by this
    check — must not raise (and must not scan the whole file to find out)."""
    n_rows = _LARGE_FILE_ROW_THRESHOLD + 1000
    lines = ["a,b,c"] + [f"{i},{i},{i}" for i in range(n_rows)]
    # Header is line 1, data starts at line 2, so line (2 + sample_rows) is
    # the first line past the validated sample.
    past_sample_idx = _VALIDATION_SAMPLE_ROWS + 5
    lines[past_sample_idx] = "1,2,3,4"
    text = "\n".join(lines) + "\n"
    _validate_row_lengths(text, ",", "large_bad_late.csv")  # must not raise


def test_validate_row_lengths_small_file_still_fully_validated() -> None:
    """Below the threshold, behavior is unchanged — every row checked,
    including one deep into a file that's still well under the threshold."""
    n_rows = 100
    lines = ["a,b,c"] + [f"{i},{i},{i}" for i in range(n_rows)]
    lines[-1] = "1,2,3,4"  # last row, well past _VALIDATION_SAMPLE_ROWS if it applied
    text = "\n".join(lines) + "\n"
    with pytest.raises(ValueError, match="expected 3 fields"):
        _validate_row_lengths(text, ",", "small_bad_late.csv")


def test_validate_row_lengths_wraps_csv_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A genuine `csv.Error` from the stdlib reader itself (not just a field
    # count mismatch) — shrink the field-size limit so an ordinary field
    # trips it, then confirm it surfaces as our actionable ValueError.
    import csv as csv_module

    original_limit = csv_module.field_size_limit()
    csv_module.field_size_limit(5)
    try:
        text = "a,b\nlongvalue,2\n"
        with pytest.raises(ValueError, match="Malformed CSV quoting"):
            _validate_row_lengths(text, ",", "toolong.csv")
    finally:
        csv_module.field_size_limit(original_limit)


# --- No-regression: a normal, well-formed CSV is unaffected ---


def test_load_csv_well_formed_file_unaffected(tmp_path: Path) -> None:
    path = tmp_path / "clean.csv"
    path.write_text(
        'nome,cidade,valor\nAna,"Recife, PE",10.5\nBruno,Salvador,20.0\n', encoding="utf-8"
    )
    df = load_csv(str(path))
    assert df.shape == (2, 3)
    assert df.loc[0, "cidade"] == "Recife, PE"


# --- Excel: multi-sheet, merged cells, header offset ---
# Before the fix: `pd.read_excel(path)` on a multi-sheet workbook silently
# read only the first sheet with no indication other sheets existed;
# on the header-offset/merged-cell fixtures it silently treated the title
# row as column names and the real header as a data row. All confirmed by
# direct reproduction.


def test_load_excel_multi_sheet_without_explicit_choice_raises() -> None:
    with pytest.raises(ValueError, match="has 2 sheets"):
        load_csv(str(FIXTURES / "excel_multi_sheet.xlsx"))


def test_load_excel_multi_sheet_with_explicit_sheet_name() -> None:
    df = load_csv(str(FIXTURES / "excel_multi_sheet.xlsx"), sheet_name="Estoque")
    assert list(df.columns) == ["produto", "estoque_atual"]
    assert df.shape == (2, 2)


def test_load_excel_merged_cells_detects_real_header() -> None:
    df = load_csv(str(FIXTURES / "excel_merged_cells.xlsx"))
    assert list(df.columns) == ["produto", "quantidade", "preco"]
    assert df.shape == (2, 3)


def test_load_excel_header_offset_detects_real_header() -> None:
    df = load_csv(str(FIXTURES / "excel_header_offset.xlsx"))
    assert list(df.columns) == ["produto", "quantidade", "preco"]
    assert df.shape == (2, 3)
    assert df.loc[0, "produto"] == "Notebook"


def test_load_excel_header_row_explicit_override() -> None:
    df = load_csv(str(FIXTURES / "excel_header_offset.xlsx"), header_row=3)
    assert list(df.columns) == ["produto", "quantidade", "preco"]


def test_load_excel_no_confident_header_row_raises(tmp_path: Path) -> None:
    # A sheet with no row that fills every column with text-only values —
    # every row has at least one numeric cell, so the heuristic finds nothing.
    path = tmp_path / "no_header.xlsx"
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    df.to_excel(path, index=False, header=False)
    with pytest.raises(ValueError, match="Could not locate a header row"):
        load_csv(str(path))
