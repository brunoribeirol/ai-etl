"""Unit tests for the storage backend abstraction (storage.py, ADR-009).

`S3StorageBackend` is tested against a mocked `boto3` client (no `moto`
dependency in this project) — these tests assert the right S3 API calls with
the right Bucket/Key are made, not against a live/emulated bucket. Coverage of
the actual bucket/IAM setup is manual (ADR-009), not something a unit test
can verify.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from ai_etl.audit.storage import LocalStorageBackend, S3StorageBackend, get_storage_backend

# ---------------------------------------------------------------------------
# LocalStorageBackend — today's exact Path-based behavior, unchanged
# ---------------------------------------------------------------------------


def test_local_backend_round_trips_bytes(tmp_path: Path) -> None:
    backend = LocalStorageBackend(str(tmp_path))
    backend.write_bytes("run-1.json", b'{"ok": true}')

    assert backend.exists("run-1.json")
    assert backend.read_bytes("run-1.json") == b'{"ok": true}'


def test_local_backend_missing_key_does_not_exist(tmp_path: Path) -> None:
    backend = LocalStorageBackend(str(tmp_path))
    assert backend.exists("no-such-key.json") is False


def test_local_backend_creates_base_dir(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested"
    LocalStorageBackend(str(nested))
    assert nested.exists()


def test_local_backend_writes_under_nested_key(tmp_path: Path) -> None:
    """Keys with a `/` (mirroring the S3 backend's tenant/environment prefix
    shape) still resolve under `base_dir`, not at the filesystem root."""
    backend = LocalStorageBackend(str(tmp_path))
    backend.write_bytes("sub/dir/file.csv", b"a,b\n1,2\n")

    assert (tmp_path / "sub" / "dir" / "file.csv").exists()


def test_local_backend_delete_bytes_removes_the_key(tmp_path: Path) -> None:
    backend = LocalStorageBackend(str(tmp_path))
    backend.write_bytes("run-1.json", b"{}")

    backend.delete_bytes("run-1.json")

    assert backend.exists("run-1.json") is False


def test_local_backend_delete_bytes_is_a_noop_for_missing_key(tmp_path: Path) -> None:
    """ADR-025: candidate keys derived from DB rows may legitimately not
    exist for every run (e.g. no transform code) — deletion must not raise."""
    backend = LocalStorageBackend(str(tmp_path))

    backend.delete_bytes("no-such-key.json")  # must not raise


def test_local_backend_list_existing_keys_returns_relative_paths(tmp_path: Path) -> None:
    backend = LocalStorageBackend(str(tmp_path))
    backend.write_bytes("run-1.json", b"{}")
    backend.write_bytes("sub/dir/run-2.json", b"{}")

    assert backend.list_existing_keys() == {"run-1.json", "sub/dir/run-2.json"}


def test_local_backend_list_existing_keys_empty_when_nothing_written(tmp_path: Path) -> None:
    backend = LocalStorageBackend(str(tmp_path))

    assert backend.list_existing_keys() == set()


# ---------------------------------------------------------------------------
# S3StorageBackend — mocked boto3 client
# ---------------------------------------------------------------------------


def _make_backend_with_mock_client(
    bucket: str = "ai-etl-artifacts-brlla", prefix: str = "prod/tenant-a"
):
    with patch("boto3.client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client
        backend = S3StorageBackend(bucket=bucket, prefix=prefix)
    return backend, mock_client


def test_s3_backend_write_bytes_puts_object_under_prefixed_key() -> None:
    backend, mock_client = _make_backend_with_mock_client()
    backend.write_bytes("run-1.json", b'{"ok": true}')

    mock_client.put_object.assert_called_once_with(
        Bucket="ai-etl-artifacts-brlla", Key="prod/tenant-a/run-1.json", Body=b'{"ok": true}'
    )


def test_s3_backend_read_bytes_gets_object_under_prefixed_key() -> None:
    backend, mock_client = _make_backend_with_mock_client()
    mock_body = MagicMock()
    mock_body.read.return_value = b'{"ok": true}'
    mock_client.get_object.return_value = {"Body": mock_body}

    data = backend.read_bytes("run-1.json")

    mock_client.get_object.assert_called_once_with(
        Bucket="ai-etl-artifacts-brlla", Key="prod/tenant-a/run-1.json"
    )
    assert data == b'{"ok": true}'


def test_s3_backend_exists_true_when_head_object_succeeds() -> None:
    backend, mock_client = _make_backend_with_mock_client()
    mock_client.head_object.return_value = {}

    assert backend.exists("run-1.json") is True
    mock_client.head_object.assert_called_once_with(
        Bucket="ai-etl-artifacts-brlla", Key="prod/tenant-a/run-1.json"
    )


def test_s3_backend_exists_false_on_404() -> None:
    backend, mock_client = _make_backend_with_mock_client()
    mock_client.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )

    assert backend.exists("no-such-key.json") is False


def test_s3_backend_exists_reraises_non_404_errors() -> None:
    backend, mock_client = _make_backend_with_mock_client()
    mock_client.head_object.side_effect = ClientError(
        {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject"
    )

    with pytest.raises(ClientError):
        backend.exists("run-1.json")


def test_s3_backend_list_existing_keys_strips_the_prefix() -> None:
    """Perf fix (2026-09-03): `list_existing_keys` must return keys the same
    shape `exists(key)` takes (relative to this instance's own prefix), not
    the raw S3 object keys, so a caller can do `key in list_existing_keys()`
    as a drop-in replacement for `exists(key)`."""
    backend, mock_client = _make_backend_with_mock_client()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {
            "Contents": [
                {"Key": "prod/tenant-a/run-1.json"},
                {"Key": "prod/tenant-a/run-1_transform.py"},
            ]
        }
    ]
    mock_client.get_paginator.return_value = mock_paginator

    keys = backend.list_existing_keys()

    mock_client.get_paginator.assert_called_once_with("list_objects_v2")
    mock_paginator.paginate.assert_called_once_with(
        Bucket="ai-etl-artifacts-brlla", Prefix="prod/tenant-a/"
    )
    assert keys == {"run-1.json", "run-1_transform.py"}


def test_s3_backend_list_existing_keys_paginates_across_multiple_pages() -> None:
    backend, mock_client = _make_backend_with_mock_client()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {"Contents": [{"Key": "prod/tenant-a/run-1.json"}]},
        {"Contents": [{"Key": "prod/tenant-a/run-2.json"}]},
    ]
    mock_client.get_paginator.return_value = mock_paginator

    keys = backend.list_existing_keys()

    assert keys == {"run-1.json", "run-2.json"}


def test_s3_backend_list_existing_keys_empty_bucket_returns_empty_set() -> None:
    """A page with no `Contents` key at all (S3's actual shape for an empty
    prefix, not an empty list) must not raise a `KeyError`."""
    backend, mock_client = _make_backend_with_mock_client()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{}]
    mock_client.get_paginator.return_value = mock_paginator

    assert backend.list_existing_keys() == set()


def test_s3_backend_delete_bytes_deletes_object_under_prefixed_key() -> None:
    backend, mock_client = _make_backend_with_mock_client()

    backend.delete_bytes("run-1.json")

    mock_client.delete_object.assert_called_once_with(
        Bucket="ai-etl-artifacts-brlla", Key="prod/tenant-a/run-1.json"
    )


def test_s3_backend_scopes_keys_by_environment_and_tenant_prefix() -> None:
    """The whole point of ADR-009's key shape: two tenants (or two environments)
    never collide in the same bucket."""
    backend_a, client_a = _make_backend_with_mock_client(prefix="prod/tenant-a")
    backend_b, client_b = _make_backend_with_mock_client(prefix="prod/tenant-b")

    backend_a.write_bytes("run-1.json", b"a")
    backend_b.write_bytes("run-1.json", b"b")

    assert client_a.put_object.call_args.kwargs["Key"] == "prod/tenant-a/run-1.json"
    assert client_b.put_object.call_args.kwargs["Key"] == "prod/tenant-b/run-1.json"


# ---------------------------------------------------------------------------
# get_storage_backend — STORAGE_BACKEND env-driven selection
# ---------------------------------------------------------------------------


def test_get_storage_backend_defaults_to_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)

    backend = get_storage_backend(str(tmp_path), tenant_id="tenant-a")

    assert isinstance(backend, LocalStorageBackend)


def test_get_storage_backend_s3_requires_bucket_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.delenv("AI_ETL_S3_BUCKET", raising=False)

    with pytest.raises(EnvironmentError):
        get_storage_backend("./runs", tenant_id="tenant-a")


def test_get_storage_backend_s3_scopes_by_environment_and_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AI_ETL_S3_BUCKET", "ai-etl-artifacts-brlla")
    monkeypatch.setenv("AI_ETL_ENV", "prod")

    with patch("boto3.client"):
        backend = get_storage_backend("./runs", tenant_id="tenant-a")

    assert isinstance(backend, S3StorageBackend)
    assert backend.bucket == "ai-etl-artifacts-brlla"
    assert backend.prefix == "prod/tenant-a"


def test_get_storage_backend_s3_defaults_environment_to_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AI_ETL_S3_BUCKET", "ai-etl-artifacts-brlla")
    monkeypatch.delenv("AI_ETL_ENV", raising=False)

    with patch("boto3.client"):
        backend = get_storage_backend("./runs", tenant_id="tenant-a")

    assert backend.prefix == "dev/tenant-a"


def test_get_storage_backend_s3_falls_back_to_no_tenant_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers that don't pass a tenant_id (e.g. the CLI entry point) still land
    in a stable, non-colliding location rather than at the prefix root."""
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AI_ETL_S3_BUCKET", "ai-etl-artifacts-brlla")
    monkeypatch.setenv("AI_ETL_ENV", "prod")

    with patch("boto3.client"):
        backend = get_storage_backend("./runs", tenant_id=None)

    assert backend.prefix == "prod/_no_tenant"
