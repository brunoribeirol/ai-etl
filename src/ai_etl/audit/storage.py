"""Storage backend abstraction for audit artifacts (ADR-009).

`audit/db.py` persists `./runs/` artifacts (run JSON, Silver CSV, Gold/Science
CSVs and figures) by calling this module instead of `pathlib`/`pandas` file I/O
directly. Backend is selected via `STORAGE_BACKEND` (`local` | `s3`, default
`local` — no behavior change for anyone who doesn't opt in).

`S3StorageBackend` uses `boto3`, not `pyarrow.fs.S3FileSystem` — see ADR-009 for
why (pyarrow's bundled S3 client hung indefinitely in this same kind of
environment even with correct IAM, reproduced on two independent machines in a
sibling project; boto3 reuses the same code path already proven via the AWS
CLI). Keys are prefixed `{AI_ETL_ENV}/{tenant_id}/...` so a locally-run dev
session and the real production deployment can never collide in the same
bucket, without provisioning separate infrastructure for it.
"""

import os
from pathlib import Path
from typing import Protocol


class StorageBackend(Protocol):
    """Minimal key/bytes storage interface `audit/db.py` writes/reads artifacts through."""

    def write_bytes(self, key: str, data: bytes) -> None: ...

    def read_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


class LocalStorageBackend:
    """Wraps `Path(log_dir) / key` — today's exact behavior, unchanged."""

    def __init__(self, log_dir: str):
        self.base_dir = Path(log_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self.base_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_bytes(self, key: str) -> bytes:
        return (self.base_dir / key).read_bytes()

    def exists(self, key: str) -> bool:
        return (self.base_dir / key).exists()


class S3StorageBackend:
    """`boto3`-backed storage, scoped under `{environment}/{tenant_id}/` in one bucket.

    Credentials/region are resolved by `boto3`'s default chain (env vars
    `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_DEFAULT_REGION` in this
    project's deployment — see ADR-009) — nothing project-specific to configure
    here beyond the bucket name and key prefix.
    """

    def __init__(self, bucket: str, prefix: str):
        import boto3

        self._client = boto3.client("s3")
        self.bucket = bucket
        self.prefix = prefix

    def _full_key(self, key: str) -> str:
        return f"{self.prefix}/{key}"

    def write_bytes(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=self._full_key(key), Body=data)

    def read_bytes(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=self._full_key(key))
        return response["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=self._full_key(key))
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise


def get_storage_backend(log_dir: str, tenant_id: str | None) -> StorageBackend:
    """Build the `StorageBackend` for one call site, per `STORAGE_BACKEND`.

    Args:
        log_dir: Directory `LocalStorageBackend` writes into (`local` mode only —
            ignored in `s3` mode, where the bucket is the storage root).
        tenant_id: Same tenant-scoping value `audit/db.py`'s callers already pass
            around (Clerk user id — see ADR-006). Used as the second path segment
            of the S3 key prefix; `None` falls back to a literal `"_no_tenant"`
            segment so callers that don't pass one (e.g. the CLI entry point)
            still land in a stable, non-colliding location rather than at the
            environment prefix's root.
    """
    backend = os.getenv("STORAGE_BACKEND", "local")
    if backend == "s3":
        bucket = os.getenv("AI_ETL_S3_BUCKET")
        if not bucket:
            raise EnvironmentError("AI_ETL_S3_BUCKET is required when STORAGE_BACKEND=s3")
        environment = os.getenv("AI_ETL_ENV", "dev")
        tenant_segment = tenant_id or "_no_tenant"
        return S3StorageBackend(bucket=bucket, prefix=f"{environment}/{tenant_segment}")
    return LocalStorageBackend(log_dir)
