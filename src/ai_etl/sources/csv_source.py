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
   as strictly worse than a loud failure. Post-merge code review measured
   this second pass doubling `load_csv`'s CPU time against the Sprint 12
   204k-row benchmark, so it's full-file below `_LARGE_FILE_ROW_THRESHOLD`
   rows and a bounded leading sample above it — an explicit, documented
   trade-off (see the function's own docstring), not a silent one.

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

Data-eng audit (2026-08-24) added two more fixes on top of the above:

4. **Field-size-limit false positive.** A genuinely well-formed row with one
   legitimate field over the stdlib `csv` module's 128 KiB default limit
   raised `_csv.Error: field larger than field limit (131072)`, caught by
   the same generic `except csv.Error` as point 3 above and mislabeled
   "Malformed CSV quoting" — misleading, since the file isn't malformed.
   `_validate_row_lengths()` now raises the effective limit (bounded, not
   unlimited) and gives the residual case its own accurate message.

5. **BR-locale decimal commas.** A `;`-delimited file (BR-locale export
   convention: `;` delimiter, `,` decimal separator) with a numeric column
   like `"80,50"` stays `object` dtype — pandas has no way to know that's a
   locale decimal separator rather than arbitrary text.
   `_normalize_br_decimal_commas()` converts such columns to float, but only
   for `;`-delimited files and only when every non-null value in the column
   matches the decimal-comma pattern, to avoid false-positive conversion of
   legitimate `,`-containing text.
"""

import csv
import io
import itertools
import re
from collections.abc import Iterable

import pandas as pd
from charset_normalizer import from_bytes

# csv.Sniffer's own default candidate set is a superset that misfires on
# ordinary prose; restrict it to delimiters real tabular exports actually use.
_CANDIDATE_DELIMITERS = ",;\t|"

# BR-locale numeric literal: `;` field delimiter + `,` decimal separator
# (e.g. "80,50", "-1234,56"), contrasting with the US `,` delimiter + `.`
# decimal convention. Anchored full-string match — a column only qualifies
# if *every* non-null value matches this exactly.
_BR_DECIMAL_COMMA_PATTERN = re.compile(r"^-?\d+,\d+$")

# How many rows of an Excel sheet to scan when looking for the real header
# row — generous enough for a few title/spacer rows without scanning an
# entire large sheet for no benefit.
_MAX_HEADER_SCAN_ROWS = 20

# Sprint 22 code-review finding: `_validate_row_lengths()` re-walking every
# row of a file in pure Python (on top of pandas' own C-engine parse just
# after it) measurably doubled `extractor_load_csv`'s CPU time against the
# Sprint 12 204k-row benchmark (`case_study/data/profile_scale.py`) — the
# exact metric that sprint optimized. Below this row-count threshold, every
# row is still validated (unchanged, full guarantee) — this covers the
# realistic case for the malformed-quote/stray-delimiter bug the check
# exists to catch: small, hand-edited, or ad-hoc exports, not large
# machine-generated bulk extracts. Above it, only a bounded leading sample
# (`_VALIDATION_SAMPLE_ROWS`) is validated — an explicit, documented
# trade-off, not a silent one (see `_validate_row_lengths()`'s docstring).
# Chosen to match the order of magnitude Sprint 12/ADR-013 already
# established for sandbox timeout scaling (`LARGE_DATASET_ROW_THRESHOLD` in
# `core/sandbox.py`), for a consistent "large dataset" definition across the
# pipeline rather than a second, unrelated number.
_LARGE_FILE_ROW_THRESHOLD = 50_000
_VALIDATION_SAMPLE_ROWS = 5_000

# Data-eng audit finding (2026-08-24): the stdlib `csv` module's default
# per-field size limit is 128 KiB (`_csv.Error: field larger than field
# limit (131072)`) — real protection against unbounded-field-growth DoS, but
# tight enough that a genuinely well-formed row with one large legitimate
# field (a long free-text/JSON/description column) trips it. Before this
# fix, that landed in the generic `except csv.Error` below and was
# mislabeled "Malformed CSV quoting", which is actively misleading for a
# file that isn't malformed at all. Two mitigations, both applied: this
# multiplier raises the effective limit (still bounded, not
# `sys.maxsize` — an unbounded limit would reopen the DoS the stdlib default
# guards against) so only fields an order of magnitude larger than the
# default trip it at all; and `_validate_row_lengths()` below distinguishes
# the field-size-limit signature from a real malformed-quote `csv.Error` so
# the rare case that still hits it gets an accurate message instead.
_FIELD_SIZE_LIMIT_MULTIPLIER = 10
_FIELD_SIZE_LIMIT_SIGNATURE = "field larger than field limit"


def load_csv(
    path: str,
    sheet_name: str | int | None = None,
    header_row: int | None = None,
) -> pd.DataFrame:
    """Load a CSV, Excel, or flat-records JSON file into a DataFrame.

    `sheet_name`/`header_row` are Excel-only and optional — omitting them
    preserves auto-detection (see module docstring); passing them lets a
    caller resolve an ambiguity explicitly instead of relying on the
    heuristics. Ignored for CSV/JSON.

    Real bug found 2026-08-30, live testing: the Orchestrator's source-type
    enum (`agents/pipeline/orchestrator.py::ORCHESTRATOR_PROMPT`) has no
    `"json"` value — a `.json` upload plans as `"csv"` (the closest fit,
    same as this module already handles `.xlsx`/`.xls` under `"csv"`), so
    it must actually work here rather than fail with a "malformed CSV"
    error from trying to parse JSON text as delimited text.
    """
    if path.endswith((".xlsx", ".xls")):
        return _load_excel(path, sheet_name, header_row)
    if path.endswith(".json"):
        return _load_json(path)
    return _load_csv_file(path)


def _load_json(path: str) -> pd.DataFrame:
    """Load a flat JSON array-of-records file (`df.to_json(orient="records")`
    shape — the common case for a tabular JSON export). A top-level JSON
    object or a deeply nested structure raises a clear, specific error
    rather than `pandas` silently producing a one-row/one-column DataFrame
    that doesn't match what the source data actually looks like — same
    "loud, specific failure over a silently wrong DataFrame" posture this
    module's CSV path already takes (see module docstring, failure class 3).
    """
    try:
        df = pd.read_json(path, orient="records")
    except ValueError as exc:
        raise ValueError(
            f"Could not read '{path}' as a flat JSON array of records: {exc}. "
            f"Expected a top-level JSON array of objects (e.g. "
            f'[{{"col1": 1, "col2": "a"}}, ...]) — a top-level object or a '
            f"deeply nested structure isn't supported by this source type."
        ) from exc
    if df.empty or len(df.columns) == 0:
        raise ValueError(
            f"'{path}' parsed to an empty table — expected a non-empty JSON array of row objects."
        )
    return df


def _load_csv_file(path: str) -> pd.DataFrame:
    with open(path, "rb") as f:
        raw = f.read()
    text = _decode_bytes(raw, path)

    delimiter = _detect_delimiter(text)
    _validate_row_lengths(text, delimiter, path)

    df = pd.read_csv(io.StringIO(text), sep=delimiter)
    if delimiter == ";":
        df = _normalize_br_decimal_commas(df)
    return df


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


def _normalize_br_decimal_commas(df: pd.DataFrame) -> pd.DataFrame:
    """Convert BR-locale decimal-comma numeric columns (object dtype) to float.

    Only called for `;`-delimited files (see `_load_csv_file`) — that's the
    realistic signal for a BR-locale export, so this never runs against an
    ordinary `,`-delimited file where a `,`-containing text column is a much
    more plausible explanation than a decimal separator. Data-eng audit
    finding (2026-08-24): pandas has no way to know a `,`-containing string
    column like `"80,50"` is actually numeric with a locale decimal
    separator — it stays `object` dtype, silently unusable for downstream
    numeric ops (quality checks, aggregations, etc.) without this.

    Conservative by design: a column converts only if *every* non-null value
    matches `_BR_DECIMAL_COMMA_PATTERN` and the comma-to-dot swap parses
    cleanly. Any column with even one genuinely non-numeric value (free
    text, an ID, a mixed column) is left exactly as pandas produced it — no
    partial/coerced conversion that could silently corrupt real data.
    """
    for col in df.columns:
        series = df[col]
        if series.dtype != object:
            continue
        non_null = series.dropna().astype(str)
        if non_null.empty or not non_null.str.match(_BR_DECIMAL_COMMA_PATTERN).all():
            continue

        candidate = series.astype(str).str.replace(",", ".", regex=False)
        converted = pd.to_numeric(candidate, errors="coerce")
        # Every non-null value already matched the strict pattern above, so
        # a new NaN here would mean `to_numeric` still couldn't parse a
        # value the regex accepted — shouldn't happen, but bail rather than
        # risk silently dropping a value.
        if int(converted.isna().sum()) != int(series.isna().sum()):
            continue
        df[col] = converted
    return df


def _validate_row_lengths(text: str, delimiter: str, path: str) -> None:
    """Reject a file whose data rows don't match the header's field count.

    pandas' C engine can silently accept and misalign such rows (confirmed
    against `tests/fixtures/dirty_data/csv_ambiguous_delimiter.csv` — no
    exception, wrong values in every column) instead of raising. Re-walking
    the raw text with the stdlib `csv` module — which does enforce
    consistent quoting — catches both root causes in one check: a quote
    character appearing mid-field (not at the start), and a stray
    unescaped delimiter inside what should have been one field.

    Sprint 22 code-review fix: below `_LARGE_FILE_ROW_THRESHOLD` lines every
    row is checked, same guarantee as before. Above it, only the first
    `_VALIDATION_SAMPLE_ROWS` rows are checked — a bounded-cost sample
    instead of a second full-file pass. This is a real, accepted trade-off:
    a malformed row that first appears past the sample boundary in a very
    large file will not be caught here. pandas' own default
    `on_bad_lines="error"` (still active on the real `read_csv` call right
    after this) remains a partial backstop, but does *not* catch the
    quote-confusion variant of this bug (confirmed by direct testing — see
    module docstring, point 3) — only a genuine extra-field row with no
    quote character involved. Large, machine-generated bulk extracts (the
    case this threshold targets) are overwhelmingly the case where that
    residual gap is acceptable; small/hand-edited files — where the bug
    this check exists for actually originates — stay fully covered.
    """
    # A cheap `str.count` to decide the strategy, not a second CSV-aware
    # pass — this is O(n) over raw text same as the read that already
    # happened, negligible next to the parse itself.
    approx_line_count = text.count("\n") + 1
    sample_limit = (
        _VALIDATION_SAMPLE_ROWS if approx_line_count > _LARGE_FILE_ROW_THRESHOLD else None
    )

    # Raised (bounded, not unlimited — see constant's docstring comment)
    # for the duration of this check only, and restored in `finally` so it
    # doesn't leak into unrelated `csv` usage elsewhere in the process.
    original_field_size_limit = csv.field_size_limit()
    csv.field_size_limit(original_field_size_limit * _FIELD_SIZE_LIMIT_MULTIPLIER)
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            return  # empty file — let pandas raise its own (accurate) error
        expected = len(header)

        numbered_rows: Iterable[tuple[int, list[str]]] = enumerate(reader, start=2)
        if sample_limit is not None:
            numbered_rows = itertools.islice(numbered_rows, sample_limit)

        try:
            for line_num, row in numbered_rows:
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
            if _FIELD_SIZE_LIMIT_SIGNATURE in str(e):
                raise ValueError(
                    f"Field too large in '{path}' near line {reader.line_num}: {e}. "
                    "This is not malformed CSV — the file appears well-formed but has "
                    "a field larger than the parser's size limit "
                    f"(currently {csv.field_size_limit()} characters, raised "
                    f"{_FIELD_SIZE_LIMIT_MULTIPLIER}x from the Python stdlib default of "
                    "131072 for this check). If this field is legitimately this large "
                    "(e.g. a long free-text/JSON column), raise the limit further via "
                    "`csv.field_size_limit()` before loading; otherwise inspect the "
                    "source row — it may be missing a delimiter or closing quote that "
                    "caused unrelated content to be absorbed into one field."
                ) from e
            raise ValueError(
                f"Malformed CSV quoting in '{path}' near line {reader.line_num}: {e}. "
                "Check for an unescaped quote character inside a field."
            ) from e
    finally:
        csv.field_size_limit(original_field_size_limit)


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
