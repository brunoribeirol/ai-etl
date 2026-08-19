"""E2E Scenario 5 — SQLite + authenticated REST sources, CSV destination
(Sprint 11, ADR-012).

New for Sprint 11: a real SQLite `.db` file (seeded for real via the stdlib
`sqlite3` module — not mocked) as one source, and a REST source using bearer
auth as the other. Same convention as scenario 3's REST call: only the
network transport (`httpx.get`) is mocked, everything else (auth header
construction, SQLite read, sandboxed transform, quality checks, CSV write,
Postgres-backed audit trail) runs for real through the full stack.
"""

import sqlite3

import pandas as pd

from ai_etl.audit.db import load_full_result, load_history
from ai_etl.services.execution_queue import enqueue_analysis, get_task_status
from tests.e2e.conftest import requires_full_stack

TRANSFORM_CODE = """
def transform(dfs: dict) -> pd.DataFrame:
    orders = dfs["orders"]
    rates = dfs["rates"]
    usd_rate = rates["usd_rate"].iloc[0]
    df = orders.copy()
    df["revenue_usd"] = df["revenue"] * usd_rate
    return df
"""


@requires_full_stack
def test_scenario5_sqlite_rest_auth_runs_end_to_end(
    tmp_path, mock_pipeline_llm, test_tenant, monkeypatch, mocker
) -> None:
    # --- real SQLite source, seeded for real ---
    db_path = tmp_path / "shop.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, product TEXT, revenue REAL)")
        conn.executemany(
            "INSERT INTO orders (id, product, revenue) VALUES (?, ?, ?)",
            [(1, "A", 100.0), (2, "B", 200.0)],
        )
        conn.commit()

    # --- authenticated REST source (network mocked, auth logic real) ---
    monkeypatch.setenv("EXCHANGE_API_TOKEN", "test-token-abc")
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = {"usd_rate": 5.0}
    mock_response.raise_for_status.return_value = None
    mock_get = mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=mock_response)

    output_path = tmp_path / "output.csv"
    plan = {
        "sources": [
            {"name": "orders", "type": "sqlite", "path": str(db_path), "table": "orders"},
            {
                "name": "rates",
                "type": "rest",
                "url": "https://example.com/exchange-rate",
                "auth": {"type": "bearer", "env_var": "EXCHANGE_API_TOKEN"},
            },
        ],
        "destination": {"type": "csv", "path": str(output_path)},
        "transformations": ["convert revenue to USD using the exchange rate"],
        "quality_checks": ["null_check"],
    }
    mock_pipeline_llm(plan, TRANSFORM_CODE)

    spec = (
        f"Read the orders table from the SQLite database {db_path}. Fetch the "
        "current exchange rate from the authenticated exchange-rate API. "
        f"Convert revenue to USD. Save the result to {output_path}."
    )
    tenant_id = test_tenant["tenant_id"]

    task_id = enqueue_analysis(
        spec, business_question="", run_dir=str(tmp_path), tenant_id=tenant_id
    )
    status = get_task_status(task_id)

    assert status["state"] == "SUCCESS", status.get("error")
    assert status["result"]["status"] == "completed"

    # The REST call actually happened with the bearer token attached — not
    # just that the mocked response was accepted.
    _, kwargs = mock_get.call_args
    assert kwargs["headers"] == {"Authorization": "Bearer test-token-abc"}

    # Loader wrote the SQLite-sourced, REST-enriched result for real.
    result_df = pd.read_csv(output_path)
    assert list(result_df["revenue_usd"]) == [500.0, 1000.0]

    history = load_history(tenant_id=tenant_id)
    assert status["result"]["run_id"] in history["run_id"].tolist()

    full_result = load_full_result(
        status["result"]["run_id"], log_dir=str(tmp_path), tenant_id=tenant_id
    )
    assert full_result is not None
    assert full_result["state"]["status"] == "completed"
