"""Auto-generated NL pipeline spec from an uploaded file (extracted from `app.py`,
Sprint 6/ADR-011 — needed by both the Streamlit UI and the new API's `POST /runs`,
same reasoning `pipeline_service.py`'s own docstring already gives for extracting
orchestration logic out of `app.py`: unusable from any caller that isn't Streamlit
otherwise. No Streamlit dependency, no behavior change from the original.
"""

from pathlib import Path

import pandas as pd


def auto_generate_spec(
    file_path: Path,
    df: pd.DataFrame,
    output_csv: Path,
    business_question: str = "",
) -> str:
    cols = ", ".join(df.columns.tolist())
    n_rows, n_cols = df.shape
    question_hint = f" A análise responderá: {business_question}." if business_question else ""
    return (
        f"Read the file at {file_path}. "
        f"The file has {n_rows} rows and {n_cols} columns: {cols}.{question_hint} "
        f"Clean the data: remove completely duplicate rows, standardize column names to snake_case, "
        f"parse date columns where obvious. "
        f"For missing values in non-critical numeric columns, fill with a reasonable default "
        f"(e.g. 0 or the column mean) only when that is a safe assumption. "
        f"Do NOT fabricate values for missing identifying or categorical fields "
        f"(e.g. name, id, category) — never invent a name, category, or price. "
        f"Instead, add a boolean column 'is_incomplete' set to true for rows with missing "
        f"critical fields, and leave the original value missing (NaN) in those rows. "
        f"Preserve all rows and all original columns. "
        f"Save the cleaned result to {output_csv}."
    )
