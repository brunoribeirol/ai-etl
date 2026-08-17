"""E2E Scenario 1 — CSV only, through the full stack (auth/tenancy/sandbox/async).

Mirrors `case_study/pipelines/scenario1_spec.txt` (rename dt->date, amt->amount,
filter status=="active", drop duplicates), run through `enqueue_analysis` — the
same entry point `app.py`'s "Executar" tab uses — with a real Clerk-shaped
session token, real Celery task round-trip (eager), real sandboxed transform
execution, and a real read-back via `load_history`/`load_full_result`, exactly
what the History tab does after a run completes.
"""

import pandas as pd

from ai_etl.audit.db import load_full_result, load_history
from ai_etl.services.execution_queue import enqueue_analysis, get_task_status
from tests.e2e.conftest import requires_full_stack

SPEC = (
    'Read the file {csv_path}. Rename the column "dt" to "date" and the '
    'column "amt" to "amount". Filter rows where status is "active". Remove '
    "duplicate rows. Save the result to {output_path}."
)

PLAN = {
    "sources": [{"name": "sales", "type": "csv", "path": "__CSV_PATH__"}],
    "destination": {"type": "csv", "path": "__OUTPUT_PATH__"},
    "transformations": [
        "rename dt to date",
        "rename amt to amount",
        'filter status == "active"',
        "drop duplicate rows",
    ],
    "quality_checks": ["null_check", "duplicate_check"],
}

TRANSFORM_CODE = """
def transform(dfs: dict) -> pd.DataFrame:
    df = dfs["sales"].rename(columns={"dt": "date", "amt": "amount"})
    df = df[df["status"] == "active"]
    df = df.drop_duplicates()
    return df
"""


@requires_full_stack
def test_scenario1_csv_runs_end_to_end(tmp_path, mock_pipeline_llm, test_tenant) -> None:
    csv_path = tmp_path / "sales.csv"
    output_path = tmp_path / "output.csv"
    pd.DataFrame(
        {
            "dt": ["2026-01-01", "2026-01-02", "2026-01-02", "2026-01-03"],
            "amt": [100.0, 200.0, 200.0, 300.0],
            "status": ["active", "active", "active", "cancelled"],
        }
    ).to_csv(csv_path, index=False)

    plan = {
        **PLAN,
        "sources": [{"name": "sales", "type": "csv", "path": str(csv_path)}],
        "destination": {"type": "csv", "path": str(output_path)},
    }
    mock_pipeline_llm(plan, TRANSFORM_CODE)

    spec = SPEC.format(csv_path=csv_path, output_path=output_path)
    tenant_id = test_tenant["tenant_id"]

    task_id = enqueue_analysis(
        spec, business_question="", run_dir=str(tmp_path), tenant_id=tenant_id
    )
    status = get_task_status(task_id)

    assert status["state"] == "SUCCESS", status.get("error")
    assert status["result"]["status"] == "completed"

    # Loader wrote the deduplicated, filtered, renamed CSV for real.
    result_df = pd.read_csv(output_path)
    assert list(result_df.columns) == ["date", "amount", "status"]
    assert len(result_df) == 2  # one duplicate + one cancelled row dropped
    assert set(result_df["status"]) == {"active"}

    # Real read-back through the same path the History tab uses — proves the
    # tenant-scoped audit trail (Postgres) round-trips correctly, not just the
    # Loader's file write.
    history = load_history(tenant_id=tenant_id)
    assert status["result"]["run_id"] in history["run_id"].tolist()

    full_result = load_full_result(
        status["result"]["run_id"], log_dir=str(tmp_path), tenant_id=tenant_id
    )
    assert full_result is not None
    assert full_result["state"]["status"] == "completed"
