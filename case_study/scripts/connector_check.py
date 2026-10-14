"""Functional check of the connectors the public case study does not reach.

The case study (`run_case_study.py`) exercises the CSV, PostgreSQL and REST sources and
the CSV and PostgreSQL destinations. This script exercises the others against real
services, through the ETL layer's own entry point
(`pipeline_service.run_silver_pipeline`) and with a real model:

    1. MySQL table        -> MySQL table
    2. MongoDB collection -> MongoDB collection
    3. SQLite file        -> Parquet object in S3-compatible storage

Input of the three checks: the pinned public taxi-zone table
(`case_study/data/taxi_zones.csv`, 265 rows). Each specification asks for the zones of
one borough. After the run the destination is read back without the product (driver or
client called directly) and compared with a reference computed in plain pandas.

What this is not: a measurement. One run per connector pair says the path works end to
end; it says nothing about how often.

Every run calls a real model and costs real money, so this is started by hand
(`.github/workflows/case-study.yml`, input `job: connectors`), never by CI.

Environment:
    MYSQL_URL                 SQLAlchemy URL of a MySQL database this script may write to
    MONGODB_URI               URI of a MongoDB server this script may write to
    CONNECTOR_CHECK_BUCKET    bucket for the Parquet object (created if missing)
    AWS_ENDPOINT_URL and AWS credentials, when the storage is not AWS itself
    a provider key for the model (OPENAI_API_KEY for the default)
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import sqlite3
import sys
import time
from contextlib import ExitStack, closing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "case_study" / "data"
ZONES_CSV = DATA_DIR / "taxi_zones.csv"
RESULTS_DIR = REPO_ROOT / "case_study" / "results" / "connectors"
KEPT_COLUMNS = ["LocationID", "Borough", "Zone", "service_zone"]


# Same loading technique as run_case_study.py: case_study/scripts/ is not a package.
def _load_sibling(name: str) -> ModuleType:
    module_spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "case_study" / "scripts" / f"{name}.py"
    )
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules.setdefault(name, module)
    module_spec.loader.exec_module(module)
    return module


model_comparison = _load_sibling("model_comparison")
provider_and_access = model_comparison._provider_and_access
patch_no_audit_persistence = model_comparison._patch_no_audit_persistence

from ai_etl.services import pipeline_service  # noqa: E402


@dataclass
class CheckRecord:
    check: str
    source: str
    destination: str
    borough: str
    status: str = "skipped"
    verdict: str = "not_checked"
    attempts: int | None = None
    rows_expected: int | None = None
    rows_reported_by_loader: int | None = None
    rows_read_back: int | None = None
    columns_read_back: list[str] = field(default_factory=list)
    elapsed_s: float | None = None
    note: str = ""


def _instructions(borough: str) -> str:
    columns = ", ".join(KEPT_COLUMNS)
    return (
        f"Keep only the rows where Borough is exactly '{borough}'. "
        f"Keep the columns {columns}, with exactly those names."
    )


def reference_for(zones: pd.DataFrame, borough: str) -> list[tuple[int, str]]:
    """What the destination must hold: the zones of one borough, in plain pandas."""
    kept = zones[zones["Borough"] == borough]
    return sorted((int(row.LocationID), str(row.Zone)) for row in kept.itertuples())


def compare(written: pd.DataFrame, expected: list[tuple[int, str]]) -> tuple[str, str]:
    """Compare what was read back from a destination with the reference.

    Column names are matched without regard to case, because a destination may fold
    them; the values are compared exactly.
    """
    by_lower = {str(column).lower(): column for column in written.columns}
    missing = [name for name in ("locationid", "zone") if name not in by_lower]
    if missing:
        return "mismatch", f"columns not found in the destination: {missing}"
    pairs = sorted(
        (int(location), str(zone))
        for location, zone in zip(
            written[by_lower["locationid"]], written[by_lower["zone"]], strict=True
        )
    )
    if pairs == expected:
        return "match", ""
    return "mismatch", f"{len(pairs)} rows read back, {len(expected)} expected, or values differ"


# --- one preparation and one read-back per connector pair -----------------------------


def prepare_mysql(zones: pd.DataFrame, borough: str, work_dir: Path) -> tuple[str, Callable]:
    from sqlalchemy import MetaData, Table, create_engine

    engine = create_engine(os.environ["MYSQL_URL"])
    database = engine.url.database
    target = f"zones_{borough.lower().replace(' ', '_')}"
    zones.to_sql("taxi_zones", engine, if_exists="replace", index=False)
    Table(target, MetaData()).drop(engine, checkfirst=True)
    spec = (
        f"Read the MySQL table {database}.taxi_zones. {_instructions(borough)} "
        f"Write the result to the MySQL table {database}.{target}."
    )

    def read_back() -> pd.DataFrame:
        return pd.read_sql_table(target, engine)

    return spec, read_back


def prepare_mongodb(zones: pd.DataFrame, borough: str, work_dir: Path) -> tuple[str, Callable]:
    from pymongo import MongoClient

    database = "connector_check"
    target = f"zones_{borough.lower().replace(' ', '_')}"
    with MongoClient(os.environ["MONGODB_URI"]) as client:
        client[database]["taxi_zones"].delete_many({})
        client[database]["taxi_zones"].insert_many(zones.to_dict(orient="records"))
        client[database][target].delete_many({})
    spec = (
        f"Read the MongoDB collection taxi_zones of the database {database}. "
        f"{_instructions(borough)} "
        f"Write the result to the MongoDB collection {target} of the database {database}."
    )

    def read_back() -> pd.DataFrame:
        with MongoClient(os.environ["MONGODB_URI"]) as client:
            return pd.DataFrame(list(client[database][target].find({}, {"_id": 0})))

    return spec, read_back


def prepare_sqlite_to_s3(zones: pd.DataFrame, borough: str, work_dir: Path) -> tuple[str, Callable]:
    import boto3
    from botocore.exceptions import ClientError

    bucket = os.environ["CONNECTOR_CHECK_BUCKET"]
    key = f"connector-check/zones_{borough.lower().replace(' ', '_')}.parquet"
    database_file = work_dir / "zones.db"
    with closing(sqlite3.connect(database_file)) as connection:
        zones.to_sql("taxi_zones", connection, if_exists="replace", index=False)
        connection.commit()

    client = boto3.client("s3")
    try:
        client.create_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] not in (
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        ):
            raise
    client.delete_object(Bucket=bucket, Key=key)

    relative_file = database_file.relative_to(REPO_ROOT)
    spec = (
        f"Read the table taxi_zones of the SQLite file {relative_file}. "
        f"{_instructions(borough)} "
        f"Write the result as a Parquet file to the S3 bucket {bucket}, key {key}."
    )

    def read_back() -> pd.DataFrame:
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        return pd.read_parquet(io.BytesIO(body), engine="pyarrow")

    return spec, read_back


CHECKS: list[tuple[str, str, str, str, tuple[str, ...], Callable]] = [
    ("mysql", "mysql", "mysql", "Manhattan", ("MYSQL_URL",), prepare_mysql),
    ("mongodb", "mongodb", "mongodb", "Queens", ("MONGODB_URI",), prepare_mongodb),
    (
        "sqlite_to_s3",
        "sqlite",
        "s3_parquet",
        "Brooklyn",
        ("CONNECTOR_CHECK_BUCKET",),
        prepare_sqlite_to_s3,
    ),
]


def run_check(
    name: str,
    source: str,
    destination: str,
    borough: str,
    needs: tuple[str, ...],
    prepare: Callable,
    zones: pd.DataFrame,
    out_dir: Path,
) -> CheckRecord:
    record = CheckRecord(check=name, source=source, destination=destination, borough=borough)
    missing = [variable for variable in needs if not os.getenv(variable)]
    if missing:
        record.note = f"not set: {', '.join(missing)}"
        return record

    work_dir = out_dir / name
    work_dir.mkdir(parents=True, exist_ok=True)
    expected = reference_for(zones, borough)
    record.rows_expected = len(expected)

    try:
        spec, read_back = prepare(zones, borough, work_dir)
    except Exception as exc:  # the service itself is part of what is being checked
        record.status = "preparation_failed"
        record.note = f"{type(exc).__name__}: {exc}"[:400]
        return record

    start = time.perf_counter()
    state: dict[str, Any] = {}
    try:
        with ExitStack() as stack:
            patch_no_audit_persistence(stack)
            state = dict(pipeline_service.run_silver_pipeline(spec, run_dir=str(work_dir)))
    except Exception as exc:  # deliberate catch-all: an unhandled crash is itself a result
        record.status = "crashed"
        record.note = f"{type(exc).__name__}: {exc}"[:400]
    record.elapsed_s = round(time.perf_counter() - start, 1)

    if state:
        record.status = str(state.get("status"))
        record.attempts = state.get("transformation_attempts")
        load_result = state.get("load_result") or {}
        record.rows_reported_by_loader = load_result.get("rows_loaded")
        if state.get("error"):
            record.note = str(state["error"]).splitlines()[0][:400]
        (work_dir / "run.json").write_text(
            json.dumps(
                {
                    "spec": spec,
                    "pipeline_plan": state.get("pipeline_plan"),
                    "transformation_code": state.get("transformation_code"),
                    "quality_report": state.get("quality_report"),
                    "load_result": state.get("load_result"),
                    "error": state.get("error"),
                },
                indent=2,
                default=str,
            )
        )

    if record.status == "completed":
        try:
            written = read_back()
        except Exception as exc:
            record.verdict = "mismatch"
            record.note = f"destination could not be read back: {type(exc).__name__}: {exc}"[:400]
            return record
        record.rows_read_back = len(written)
        record.columns_read_back = [str(column) for column in written.columns]
        record.verdict, detail = compare(written, expected)
        if detail:
            record.note = detail
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--label", default=time.strftime("local-%Y%m%d-%H%M%S"))
    parser.add_argument("--only", default="", help="Comma-separated check names")
    args = parser.parse_args()

    if not ZONES_CSV.exists():
        print(f"{ZONES_CSV} missing. Run case_study/data/fetch_public_data.py", file=sys.stderr)
        return 2
    provider, has_access = provider_and_access(args.model)
    if not has_access:
        print(f"no credential for provider {provider!r}", file=sys.stderr)
        return 2
    os.environ["AI_ETL_LLM_MODEL"] = args.model.partition(":")[2] or args.model
    os.environ["AI_ETL_LLM_PROVIDER"] = provider

    zones = pd.read_csv(ZONES_CSV)
    out_dir = RESULTS_DIR / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = {name.strip() for name in args.only.split(",") if name.strip()}

    records = [
        run_check(*check, zones=zones, out_dir=out_dir)
        for check in CHECKS
        if not wanted or check[0] in wanted
    ]
    for record in records:
        print(json.dumps(asdict(record), default=str), flush=True)

    (out_dir / "summary.json").write_text(
        json.dumps(
            {"model": args.model, "checks": [asdict(record) for record in records]},
            indent=2,
            default=str,
        )
    )

    executed = [record for record in records if record.status != "skipped"]
    if not executed:
        print("nothing was executed", file=sys.stderr)
        return 2
    return 0 if all(record.verdict == "match" for record in executed) else 1


if __name__ == "__main__":
    sys.exit(main())
