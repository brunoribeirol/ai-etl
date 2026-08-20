"""CSV/Excel source connector.

Sprint 22 hardening: real customer data is not the clean, single-encoding,
comma-delimited, single-sheet-with-header-in-row-0 data the case study's
synthetic benchmarks assume. Investigated first (per this sprint's own
scope) against a small versioned corpus (`tests/fixtures/dirty_data/`)
before writing any fix — three real, reproducible failure classes were
found and are each fixed below:

1. **Encoding mismatches.** `pandas.read_csv`'s default `encoding="utf-8"`
   hard-crashes with a raw `UnicodeDecodeError` on any Latin-1/Windows-1252
   file (or a file mixing both, e.g. concatenated from two different
   editors/exports) — a common real-world artifact from Excel's regional
   "CSV (MS-DOS)"/legacy exports. `_decode_bytes()`
   retries via `charset_normalizer` (already a transitive dependency of
   `requests`, now declared directly since we import it) and only raises if
   detection itself fails, with a message naming the byte offset and the
   detected candidate if one exists.

2. **Ambiguous/wrong delimiter.** A `;`- or tab-delimited file (common in
   European/Brazilian locale exports) parses "successfully" under the
   default `sep=","` — pandas doesn't error, it just returns a single
   column containing the whole raw line, which is silently wrong, not a
   crash. `_detect_delimiter()` uses `csv.Sniffer` to pick the real
   delimiter before handing the text to pandas.

3. **Malformed quoting / stray delimiters inside unquoted fields.** This is
   the most dangerous class: pandas' C engine happily accepts a row whose
   field count doesn't match the header (a quote character appearing
   mid-field, or an extra unquoted comma) and produces a **DataFrame with
   silently shifted/wrong values** — no exception at all, confirmed against
   `tests/fixtures/dirty_data/csv_ambiguous_delimiter.csv`.
   `_validate_row_lengths()` re-walks the raw text with `csv.reader` and
   raises a specific, line-numbered error instead of ever returning that
   DataFrame — this is the case the sprint's Definition of Done singles out
   as strictly worse than a loud failure.

Excel gets equivalent treatment via `_load_excel()`: a workbook with more
than one sheet raises rather than silently reading only the first (unless
the caller passes `sheet_name` explicitly — the ambiguity is real, there is
no universally "right" sheet to guess); a header row that isn't row 0
(title rows, blank spacer rows, a merged title cell — `openpyxl`/pandas
represent a merged range as one populated cell plus `NaN` neighbors, which
looks identical to a title row for this purpose) is located by
`_detect_header_row()` instead of silently treating the title row as column
names and the real header as a data row.

This stays inside the existing CSV/Excel connector (`load_csv` already
handled `.xlsx` before this sprint) rather than becoming a new `sources/`
module — no new architecture, so no ADR (see `docs/adr/` numbering
convention: only decisions that add new connector infrastructure get one;
this is hardening of validation/parsing logic in an existing one).
"""

import csv
import io

import pandas as pd
from charset_normalizer import from_bytes

# csv.Sniffer's own default candidate set is a superset that misfires on
# ordinary prose; restrict it to delimiters real tabular exports actually use.
_CANDIDATE_DELIMITERS = ",;\t|"

# How many rows of an Excel sheet to scan when looking for the real header
# row — generous enough for a few title/spacer rows without scanning an
# entire large sheet for no benefit.
_MAX_HEADER_SCAN_ROWS = 20


def load_csv(
    path: str,
    sheet_name: str | int | None = None,
    header_row: int | None = None,
) -> pd.DataFrame:
    """Load a CSV or Excel file into a DataFrame.

    `sheet_name`/`header_row` are Excel-only and optional — omitting them
    preserves auto-detection (see module docstring); passing them lets a
    caller resolve an ambiguity explicitly instead of relying on the
    heuristics. Ignored for CSV.
    """
    if path.endswith((".xlsx", ".xls")):
        return _load_excel(path, sheet_name, header_row)
    return _load_csv_file(path)


def _load_csv_file(path: str) -> pd.DataFrame:
    with open(path, "rb") as f:
        raw = f.read()
    text = _decode_bytes(raw, path)

    delimiter = _detect_delimiter(text)
    _validate_row_lengths(text, delimiter, path)

    return pd.read_csv(io.StringIO(text), sep=delimiter)


def _decode_bytes(raw: bytes, path: str) -> str:
    """Decode file bytes to text, falling back to encoding detection.

    UTF-8 is tried first (the common case, and the fastest — no detection
    overhead). On failure, `charset_normalizer` inspects the actual byte
    statistics rather than guessing; if it can't find a confident candidate
    either, the error names the byte offset so the message is actionable
    instead of a bare traceback.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        best = from_bytes(raw).best()
        if best is not None:
            return str(best)
        raise ValueError(
            f"'{path}' is not valid UTF-8 (decode failed at byte {e.start}: {e.reason}) "
            "and no alternative encoding could be confidently detected. The file is "
            "likely truncated, binary, or uses an unusual/mixed encoding — inspect it "
            "directly, or re-export it as UTF-8."
        ) from e


def _detect_delimiter(text: str) -> str:
    """Sniff the real column delimiter instead of assuming a comma.

    Falls back to comma when the sample is too small/ambiguous for
    `csv.Sniffer` to decide (e.g. a single-column file, or the malformed-quote
    case where sniffing itself can't find a consistent pattern) — `,` is
    still pandas' own default, so this never makes an already-working file
    worse, only recovers files that were silently wrong before.
    """
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=_CANDIDATE_DELIMITERS)
        return dialect.delimiter
    except csv.Error:
        return ","


def _validate_row_lengths(text: str, delimiter: str, path: str) -> None:
    """Reject a file whose data rows don't match the header's field count.

    pandas' C engine can silently accept and misalign such rows (confirmed
    against `tests/fixtures/dirty_data/csv_ambiguous_delimiter.csv` — no
    exception, wrong values in every column) instead of raising. Re-walking
    the raw text with the stdlib `csv` module — which does enforce
    consistent quoting — catches both root causes in one check: a quote
    character appearing mid-field (not at the start), and a stray
    unescaped delimiter inside what should have been one field.
    """
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        return  # empty file — let pandas raise its own (accurate) error
    expected = len(header)

    try:
        for line_num, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != expected:
                raise ValueError(
                    f"Malformed CSV in '{path}' at line {line_num}: expected {expected} "
                    f"fields (matching header {header}), found {len(row)}: {row}. This "
                    "usually means a delimiter character appears inside an unquoted "
                    "field, or a quoted field has a stray/malformed quote. Fix the "
                    "source row, or re-export the file with properly quoted fields."
                )
    except csv.Error as e:
        raise ValueError(
            f"Malformed CSV quoting in '{path}' near line {reader.line_num}: {e}. "
            "Check for an unescaped quote character inside a field."
        ) from e


def _load_excel(path: str, sheet_name: str | int | None, header_row: int | None) -> pd.DataFrame:
    if sheet_name is None:
        available = pd.ExcelFile(path).sheet_names
        if len(available) > 1:
            raise ValueError(
                f"'{path}' has {len(available)} sheets ({available}) — there is no "
                "reliably 'right' one to guess. Pass `sheet_name` in the source "
                "config to pick one explicitly."
            )
        sheet_name = available[0]

    if header_row is None:
        raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
        header_row = _detect_header_row(raw, path, str(sheet_name))

    return pd.read_excel(path, sheet_name=sheet_name, header=header_row)


def _detect_header_row(raw: pd.DataFrame, path: str, sheet_name: str) -> int:
    """Find the real header row among leading title/blank/merged-cell rows.

    A merged title cell (`openpyxl`/pandas represent a merged range as one
    populated cell plus `NaN` in the rest of the range) and a genuine title
    row above the header look identical for this purpose: fewer populated
    cells than the sheet's real column count. The real header row is the
    first one that (a) fills every column and (b) is all text — column
    labels are always strings, whereas a real data row usually has at least
    one numeric/date column, which is what distinguishes a header from the
    first data row for an all-string-columns-wide file.
    """
    n_cols = raw.shape[1]
    scan_rows = min(_MAX_HEADER_SCAN_ROWS, len(raw))
    for i in range(scan_rows):
        row = raw.iloc[i]
        non_null = row.notna()
        if non_null.sum() == n_cols and all(isinstance(v, str) for v in row[non_null]):
            return i

    raise ValueError(
        f"Could not locate a header row in the first {scan_rows} rows of "
        f"'{path}' (sheet '{sheet_name}') — no row fills all {n_cols} columns "
        "with text-only values. Pass `header_row` explicitly in the source "
        "config, or check the sheet for a non-standard layout."
    )
