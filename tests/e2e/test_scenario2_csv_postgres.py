"""E2E Scenario 2 — CSV + Postgres source, Postgres destination.

Mirrors `case_study/pipelines/scenario2_spec.txt`: join `orders.csv` with the
Postgres `public.customers` table, filter active orders, rename `order_dt` to
`order_date`, drop null `customer_id`, write to `public.orders_cleaned`.
"""

import pandas as pd
import sqlalchemy

from ai_etl.services.execution_queue import enqueue_analysis, get_task_status
from tests.e2e.conftest import TEST_POSTGRES_URL, requires_full_stack

TRANSFORM_CODE = """
def transform(dfs: dict) -> pd.DataFrame:
    orders = dfs["orders"]
    customers = dfs["customers"]
    df = orders.merge(customers, on="customer_id", how="inner")
    df = df[df["status"] == "active"]
    df = df.rename(columns={"order_dt": "order_date"})
    df = df[df["customer_id"].notna()]
    return df
"""


@requires_full_stack
def test_scenario2_csv_postgres_runs_end_to_end(
    tmp_path, mock_pipeline_llm, test_tenant, postgres_customers_table
) -> None:
    orders_path = tmp_path / "orders.csv"
    pd.DataFrame(
        {
            "customer_id": [100, 200, 300, 999],
            "order_dt": ["2026-02-01", "2026-02-02", "2026-02-03", "2026-02-04"],
            "status": ["active", "active", "cancelled", "active"],
        }
    ).to_csv(orders_path, index=False)

    plan = {
        "sources": [
            {"name": "orders", "type": "csv", "path": str(orders_path)},
            {"name": "customers", "type": "postgres", "table": "public.customers"},
        ],
        "destination": {"type": "postgres", "table": "public.orders_cleaned"},
        "transformations": [
            "join orders and customers on customer_id",
            'filter status == "active"',
            "rename order_dt to order_date",
            "drop rows with null customer_id",
        ],
        "quality_checks": ["null_check"],
    }
    mock_pipeline_llm(plan, TRANSFORM_CODE)

    spec = (
        "Read the file orders.csv and the PostgreSQL table public.customers. "
        "Join them on customer_id. Keep only rows where order status is "
        '"active". Rename "order_dt" to "order_date". Remove rows with null '
        "customer_id. Save the result to the PostgreSQL table public.orders_cleaned."
    )
    tenant_id = test_tenant["tenant_id"]

    task_id = enqueue_analysis(
        spec, business_question="", run_dir=str(tmp_path), tenant_id=tenant_id
    )
    status = get_task_status(task_id)

    assert status["state"] == "SUCCESS", status.get("error")
    assert status["result"]["status"] == "completed"

    # Customer 999 doesn't exist in postgres_customers_table (inner join drops
    # it), customer 300's order is cancelled (filtered out) — 2 rows survive.
    engine = sqlalchemy.create_engine(TEST_POSTGRES_URL)
    written = pd.read_sql("SELECT * FROM public.orders_cleaned", engine)
    assert len(written) == 2
    assert "order_date" in written.columns
    assert set(written["customer_id"]) == {100, 200}
