"""S3-Parquet destination connector (Sprint 20, ADR-021).

The first data-warehouse-oriented destination: writes the transformed DataFrame
as a Parquet object in S3 — a de facto lake-house interchange format most
warehouses (Snowflake/BigQuery external tables, Athena, Redshift Spectrum,
Databricks) can query directly, so this doesn't compete with a customer's
existing warehouse, it lands data at its doorstep.

Same shape as `csv_dest.py`/`postgres_dest.py`: a single module-level
`save_s3_parquet(df, ..., ...) -> dict[str, Any]` with `rows_loaded`/
`destination` in the result, row count validated after the write.

Credentials: boto3's default chain (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
`AWS_DEFAULT_REGION`) — the same one `audit/storage.py::S3StorageBackend`
already uses live in production (ADR-009), nothing new to provision.
`bucket`/`key` are per-destination values from `pipeline_plan.destination`
(LLM-parsed from the spec), not a shared env var — a customer's target
bucket/prefix is destination data, not deployment config.

Serialization uses `pandas.DataFrame.to_parquet(engine="pyarrow")` into an
in-memory buffer, then a plain `boto3` `put_object` — never
`pyarrow.fs.S3FileSystem`, which ADR-009 found hangs indefinitely in this
kind of sandboxed environment even with correct IAM. `pyarrow` here is only a
serialization engine, no network I/O of its own.
"""

import io
from typing import Any

import pandas as pd


def save_s3_parquet(df: pd.DataFrame, bucket: str, key: str) -> dict[str, Any]:
    """Save DataFrame as a Parquet object in S3. Validates row count after load.

    Args:
        df: The DataFrame to write.
        bucket: Target S3 bucket name.
        key: Target S3 object key (e.g. "warehouse/sales/2026-08.parquet").

    Raises:
        RuntimeError: if the re-read row count doesn't match the source DataFrame.
    """
    import boto3

    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    body = buffer.getvalue()

    client = boto3.client("s3")
    client.put_object(Bucket=bucket, Key=key, Body=body)

    written = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    count = len(pd.read_parquet(io.BytesIO(written), engine="pyarrow"))

    if count != len(df):
        raise RuntimeError(f"Load count mismatch: expected {len(df)}, got {count}")

    return {"rows_loaded": count, "destination": f"s3://{bucket}/{key}"}
