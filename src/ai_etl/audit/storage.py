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
from typing import Protocol, cast


class StorageBackend(Protocol):
    """Minimal key/bytes storage interface `audit/db.py` writes/reads artifacts through.

    `list_existing_keys()` (2026-09-03 perf fix) exists specifically so a caller
    checking *many* candidate keys at once (`tenant_export_service.py::export_tenant_data`,
    `tenant_deletion_service.py::_delete_tenant_storage`) can do it with one backend
    call instead of one `exists()` call per candidate. On `S3StorageBackend` this is
    the difference between a single (paginated) `list_objects_v2` request and dozens
    of sequential `head_object` round-trips — confirmed as the root cause of
    `GET /tenant/export` taking 40-75s in production (2026-08-30/31 sessions) with as
    few as ~30 runs' worth of candidate keys, each `head_object` call paying a real
    cross-region network round-trip (Railway `ams` -> S3 `sa-east-1`). Returns keys
    *relative* to this backend instance's own root/prefix, matching what `exists(key)`
    already takes — a caller does `key in list_existing_keys()` instead of
    `exists(key)` in a loop.
    """

    def write_bytes(self, key: str, data: bytes) -> None: ...

    def read_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def list_existing_keys(self) -> set[str]: ...

    def delete_bytes(self, key: str) -> None:
        """Delete `key` if it exists; a no-op if it doesn't (ADR-025).

        Used by `services/tenant_deletion_service.py` to erase a tenant's
        artifacts on request. Never raises for a missing key — callers derive
        candidate keys from DB rows and some (e.g. `{run_id}_transform.py`)
        legitimately don't exist for every run.
        """
        ...


class LocalStorageBackend:
    """Files under `log_dir`, one per key. A key that leads outside `log_dir` is refused."""

    def __init__(self, log_dir: str):
        self.base_dir = Path(log_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        """Return the path of `key`, which must be inside the base directory.

        Keys are built from run ids, and a run id can arrive in the path of a request.
        The callers check that the run belongs to the tenant before they read
        (`audit/db/runs.py`); this is the second check, at the place where the file is
        opened: a key with `..`, an absolute path or a symbolic link that leads outside
        the base directory raises `ValueError`.
        """
        base = os.path.realpath(self.base_dir)
        full = os.path.realpath(os.path.join(base, key))
        if not full.startswith(base + os.sep):
            raise ValueError(f"Storage key leads outside the storage directory: {key!r}")
        return Path(full)

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_bytes(self, key: str) -> bytes:
        return self._path_for(key).read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self._path_for(key).exists()
        except ValueError:
            # Nothing outside the base directory is an object of this store.
            return False

    def list_existing_keys(self) -> set[str]:
        # Local disk I/O is cheap per-call (unlike S3's network round-trip),
        # so this is a straightforward walk rather than a perf-motivated
        # batch — kept for interface parity with S3StorageBackend so callers
        # never need to branch on which backend they're talking to.
        return {
            str(path.relative_to(self.base_dir))
            for path in self.base_dir.rglob("*")
            if path.is_file()
        }

    def delete_bytes(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)


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
        # boto3's client has no type stubs installed (ignore_missing_imports=True),
        # so response["Body"].read() types as Any — cast() documents that it's a
        # real bytes object at runtime (StreamingBody.read()'s actual return type).
        return cast(bytes, response["Body"].read())

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=self._full_key(key))
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise

    def delete_bytes(self, key: str) -> None:
        # S3's delete_object is already idempotent — deleting a missing key
        # returns 204, not an error, so no existence check is needed first.
        self._client.delete_object(Bucket=self.bucket, Key=self._full_key(key))

    def list_existing_keys(self) -> set[str]:
        """One paginated `list_objects_v2` under this instance's own prefix
        (already tenant-scoped — `self.prefix` is `{environment}/{tenant_id}`)
        instead of one `head_object` per candidate key. See the `StorageBackend`
        protocol docstring for the production latency this replaces.
        """
        paginator = self._client.get_paginator("list_objects_v2")
        prefix_with_slash = f"{self.prefix}/"
        keys: set[str] = set()
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix_with_slash):
            for obj in page.get("Contents", []):
                keys.add(obj["Key"][len(prefix_with_slash) :])
        return keys


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
