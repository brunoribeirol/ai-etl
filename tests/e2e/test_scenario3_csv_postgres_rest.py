"""E2E Scenario 3 — CSV + Postgres + REST sources, Postgres destination.

Mirrors `case_study/pipelines/scenario3_spec.txt`: join orders + customers,
enrich Recife customers with a max-temperature forecast from a REST weather
endpoint, filter active orders, write to `public.enriched_orders`.

The REST call itself is mocked (`httpx.get`, patched at `rest_source.py`'s
import site) — same reasoning as `mock_pipeline_llm` mocking the LLM calls:
this project's existing tests never depend on a real third-party network call
succeeding for a CI run to pass. `rest_source.py`'s actual JSON-normalizing
logic still runs for real against the mocked response.
"""

import pandas as pd
import sqlalchemy

from ai_etl.services.execution_queue import enqueue_analysis, get_task_status
from tests.e2e.conftest import TEST_POSTGRES_URL, requires_full_stack

WEATHER_RESPONSE = {
    "daily": {
        "time": ["2026-02-01", "2026-02-02", "2026-02-03"],
        "temperature_2m_max": [30.0, 31.5, 29.0],
    }
}

TRANSFORM_CODE = """
def transform(dfs: dict) -> pd.DataFrame:
    orders = dfs["orders"]
    customers = dfs["customers"]
    weather = dfs["weather"]
    df = orders.merge(customers, on="customer_id", how="inner")
    df = df[df["status"] == "active"]
    max_temp = weather["temperature_2m_max"].max()
    df["city_max_temp"] = df["city"].apply(lambda c: max_temp if c == "Recife" else None)
    return df
"""


@requires_full_stack
def test_scenario3_csv_postgres_rest_runs_end_to_end(
    tmp_path, mock_pipeline_llm, test_tenant, postgres_customers_table, mocker
) -> None:
    # 5 Recife customers + 1 Olinda customer (from postgres_customers_table) —
    # keeps the resulting city_max_temp null ratio (Olinda has none) at ~17%,
    # under quality.py's 20% NULL_ERROR_THRESHOLD, so this stays a "warning"
    # (Loader still runs), not a pipeline-blocking "error".
    orders_path = tmp_path / "orders.csv"
    pd.DataFrame(
        {
            "customer_id": [100, 200, 300, 400, 500, 600],
            "status": ["active"] * 6,
        }
    ).to_csv(orders_path, index=False)

    mock_response = mocker.MagicMock()
    mock_response.json.return_value = WEATHER_RESPONSE
    mock_response.raise_for_status.return_value = None
    mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=mock_response)

    weather_url = (
        "https://api.open-meteo.com/v1/forecast?latitude=-8.05&longitude=-34.88"
        "&daily=temperature_2m_max&timezone=America/Recife&forecast_days=7"
    )
    plan = {
        "sources": [
            {"name": "orders", "type": "csv", "path": str(orders_path)},
            {"name": "customers", "type": "postgres", "table": "public.customers"},
            {"name": "weather", "type": "rest", "url": weather_url},
        ],
        "destination": {"type": "postgres", "table": "public.enriched_orders"},
        "transformations": [
            "join orders and customers on customer_id",
            "add city_max_temp for Recife customers from weather forecast",
            'filter status == "active"',
        ],
        "quality_checks": ["null_check"],
    }
    mock_pipeline_llm(plan, TRANSFORM_CODE)

    spec = (
        "Read orders.csv, public.customers, and the Open-Meteo REST forecast. "
        "Join orders and customers on customer_id. Add city_max_temp for "
        "Recife customers. Filter active orders. Save to public.enriched_orders."
    )
    tenant_id = test_tenant["tenant_id"]

    task_id = enqueue_analysis(
        spec, business_question="", run_dir=str(tmp_path), tenant_id=tenant_id
    )
    status = get_task_status(task_id)

    assert status["state"] == "SUCCESS", status.get("error")
    assert status["result"]["status"] == "completed"

    engine = sqlalchemy.create_engine(TEST_POSTGRES_URL)
    written = pd.read_sql("SELECT * FROM public.enriched_orders", engine)
    assert len(written) == 6
    recife_rows = written[written["city"] == "Recife"]
    assert len(recife_rows) == 5
    assert (recife_rows["city_max_temp"] == 31.5).all()
    other_rows = written[written["city"] != "Recife"]
    assert len(other_rows) == 1
    assert other_rows["city_max_temp"].isna().all()
