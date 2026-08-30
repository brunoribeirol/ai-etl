"""Unit tests for csv_source.py's Sprint 22 dirty-data hardening.

Each test exercises `load_csv` against a real fixture in
`tests/fixtures/dirty_data/` (versioned in the repo, not gitignored — small
files, unlike the Sprint 12 synthetic scale benchmark). See
`src/ai_etl/sources/csv_source.py`'s module docstring for the three failure
classes found and fixed, and this file's own module docstring in each test
for what specifically was broken before the fix.
"""

import csv
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


def test_validate_row_lengths_field_size_limit_is_not_malformed_quoting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data-eng audit finding (2026-08-24): a `csv.Error` caused by a field
    exceeding the size limit is not malformed CSV and must not be labeled
    as such — shrink the limit so an ordinary field trips it, then confirm
    it surfaces as a distinct, accurate error instead of the malformed-
    quoting message."""
    original_limit = csv.field_size_limit()
    csv.field_size_limit(1)  # `_validate_row_lengths` multiplies this by 10 internally
    try:
        text = f"a,b\n{'x' * 100},2\n"  # 100 chars still exceeds the 10x-raised limit
        with pytest.raises(ValueError, match="Field too large") as exc_info:
            _validate_row_lengths(text, ",", "toolong.csv")
        assert "Malformed CSV quoting" not in str(exc_info.value)
    finally:
        csv.field_size_limit(original_limit)
    # The global limit must be restored, not left mutated by the check.
    assert csv.field_size_limit() == original_limit


def test_validate_row_lengths_restores_field_size_limit_on_success() -> None:
    original_limit = csv.field_size_limit()
    _validate_row_lengths("a,b\n1,2\n", ",", "ok.csv")
    assert csv.field_size_limit() == original_limit


def test_load_csv_large_legitimate_field_loads_successfully(tmp_path: Path) -> None:
    """Data-eng audit finding (2026-08-24): a genuinely well-formed CSV with
    one legitimate field larger than the stdlib default 128 KiB
    `csv.field_size_limit()` must not be rejected as malformed — the
    effective limit is raised (bounded) precisely so this loads cleanly."""
    large_field = "x" * 200_000  # well over the 131072 stdlib default
    path = tmp_path / "large_field.csv"
    path.write_text(f"id,notes,valor\n1,{large_field},10.5\n", encoding="utf-8")

    df = load_csv(str(path))

    assert df.shape == (1, 3)
    assert df.loc[0, "notes"] == large_field


def test_load_csv_genuinely_malformed_quoting_still_raises_malformed_message(
    tmp_path: Path,
) -> None:
    """No-regression: real malformed-quote input (row/header field-count
    mismatch) still raises the original 'Malformed CSV' message, not the
    new field-size-limit message."""
    path = tmp_path / "malformed.csv"
    path.write_text("a,b,c\n1,2,3,4\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed CSV") as exc_info:
        load_csv(str(path))
    assert "Field too large" not in str(exc_info.value)


# --- BR-locale decimal comma normalization ---
# Data-eng audit finding (2026-08-24): a `;`-delimited file with BR-locale
# decimal commas (e.g. "80,50") in a numeric column stayed `object` dtype —
# pandas has no way to know `,` is a decimal separator here rather than text.


def test_load_csv_semicolon_delimiter_normalizes_br_decimal_comma(tmp_path: Path) -> None:
    path = tmp_path / "br_decimal.csv"
    path.write_text(
        "nome;cidade;valor\nAna;Recife;80,50\nBruno;Salvador;1234,56\n", encoding="utf-8"
    )
    df = load_csv(str(path))
    assert pd.api.types.is_float_dtype(df["valor"])
    assert df.loc[0, "valor"] == pytest.approx(80.50)
    assert df.loc[1, "valor"] == pytest.approx(1234.56)


def test_load_csv_semicolon_delimiter_does_not_corrupt_non_numeric_comma_text(
    tmp_path: Path,
) -> None:
    """Negative test: a genuinely non-numeric text column containing commas
    in a `;`-delimited file must be left untouched, not force-converted."""
    path = tmp_path / "br_text.csv"
    path.write_text(
        "nome;observacao;valor\n"
        "Ana;Cliente antigo, prioridade alta;80,50\n"
        "Bruno;Novo, aguardando contrato;1234,56\n",
        encoding="utf-8",
    )
    df = load_csv(str(path))
    assert df["observacao"].dtype == object
    assert df.loc[0, "observacao"] == "Cliente antigo, prioridade alta"
    assert pd.api.types.is_float_dtype(df["valor"])


def test_load_csv_comma_delimiter_does_not_trigger_br_normalization(tmp_path: Path) -> None:
    """Conservative-by-design: normalization only applies to `;`-delimited
    files — a `,`-delimited file with a comma-adjacent-looking value in a
    non-numeric context must not be touched."""
    path = tmp_path / "not_br.csv"
    path.write_text('nome,codigo\nAna,"80,50"\n', encoding="utf-8")
    df = load_csv(str(path))
    assert df["codigo"].dtype == object
    assert df.loc[0, "codigo"] == "80,50"


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


# --- JSON ---
# Real bug found 2026-08-30, live testing against the deployed app: a
# `.json` upload plans as source type "csv" (the Orchestrator's own type
# enum has no "json" value — the same "closest fit" treatment `.xlsx`/`.xls`
# already get above), but `load_csv` had no `.json` branch, so it tried to
# parse the JSON text as delimited CSV text and failed with a "malformed
# CSV" error instead of actually loading the data.


def test_load_json_array_of_records(tmp_path: Path) -> None:
    path = tmp_path / "sales.json"
    pd.DataFrame({"product": ["Widget A", "Widget B"], "units_sold": [120, 85]}).to_json(
        path, orient="records", indent=2
    )

    df = load_csv(str(path))

    assert list(df.columns) == ["product", "units_sold"]
    assert df.shape == (2, 2)
    assert df.loc[0, "product"] == "Widget A"


def test_load_json_top_level_object_raises_actionable_error(tmp_path: Path) -> None:
    path = tmp_path / "not_records.json"
    path.write_text('{"product": "Widget A", "units_sold": 120}')

    with pytest.raises(ValueError, match="flat JSON array of records"):
        load_csv(str(path))


def test_load_json_empty_array_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text("[]")

    with pytest.raises(ValueError, match="parsed to an empty table"):
        load_csv(str(path))
