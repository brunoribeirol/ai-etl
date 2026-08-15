# ADR-009 — Tenant-scoped configurable storage (S3), environment-scoped config

**Status:** Accepted
**Date:** 2026-08-15
**Deciders:** Bruno Ribeiro

---

## Context

`./runs/` (artifacts persisted by `audit/db.py`'s `save_run`/`save_analysis`, read back by `load_full_result`) is local disk only today, at both the Extractor's file-upload staging (`app.py`'s `UPLOADS_DIR`) and the audit-trail JSON/CSV/figure artifacts. This is fine for a single-container deployment, but it's a real constraint on scaling past one worker/web instance (each container has its own disk — already the root cause of the cross-container upload bug fixed live during Sprint 3, see Vault bug note) and on retention/backup story for a real SaaS.

Sprint 4's roadmap scope (Vault: `artefact/sprint-roadmap.md`) is: make `./runs/` storage configurable to S3, prefixed per tenant; separate config across dev/staging/prod; fix a known CI GitHub Actions version inconsistency.

Two scoping questions were resolved with the project owner before implementation:

1. **AWS access**: an AWS account already exists; no S3 bucket yet — bucket/IAM creation is a manual step the owner does themselves (mirrors the Sprint 3 Railway worker deploy pattern), guided but not automatable by an agent without AWS credentials.
2. **"dev/staging/prod separation" scope**: explicitly **not** new infrastructure (no second Railway environment). It means environment-scoped *configuration* — a `STORAGE_BACKEND` toggle (`local` default, `s3` when configured) plus an environment-scoped key prefix in the same bucket, so a misconfigured dev run can never collide with or overwrite production data.

## Decision

Introduce `src/ai_etl/audit/storage.py`: a small storage-backend abstraction with two implementations, selected via `STORAGE_BACKEND` (`local` | `s3`, default `local` — no behavior change for anyone who doesn't opt in).

```python
class StorageBackend(Protocol):
    def write_bytes(self, key: str, data: bytes) -> None: ...
    def read_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...

class LocalStorageBackend:
    """Wraps Path(log_dir) / key — today's exact behavior, unchanged."""

class S3StorageBackend:
    """boto3, not pyarrow.fs.S3FileSystem -- see Vault decision note
    `decisions/s3-write-client-boto3-vs-pyarrow.md` from a sibling project:
    pyarrow's bundled S3 client hung indefinitely in this same kind of
    environment even with correct IAM, reproduced on two independent
    machines. boto3 reuses the same code path already proven to work via
    the AWS CLI. Same reasoning applies here -- no reason to re-litigate,
    or to re-risk the hang, on this project.

    Keys are prefixed `{environment}/{tenant_id}/...`, where `environment`
    comes from `AI_ETL_ENV` (dev|staging|prod, default `dev`) -- this is the
    entire "dev/staging/prod separation" from the roadmap: a config-level
    guarantee that a locally-run dev session and the real production
    deployment can never collide in the same bucket, without provisioning
    separate infrastructure for it.
    """
```

`audit/db.py`'s existing direct `Path.write_text()`/`df.to_csv()`/`Path.read_text()` calls in `save_run`, `save_analysis`, `_serialize_analysis_result`, `load_full_result`, and `_reload_analysis_entry` route through a `StorageBackend` obtained via a small factory (`get_storage_backend(tenant_id) -> StorageBackend`) instead of calling `pathlib`/`pandas` file I/O directly. The relative key naming convention already established (`{run_id}.json`, `{run_id}_silver.csv`, `{run_id}_gold_{i}.csv`, `{run_id}_gold_{i}_fig.json`, ...) is preserved unchanged — only *where* those keys are written changes, not their shape.

`app.py`'s upload staging (`_save_upload_to_temp`) is explicitly **out of scope** for this ADR: it writes a short-lived local file consumed synchronously by the same process before `enqueue_analysis` base64-encodes it across the Celery task boundary (the Sprint 3 interim fix) — that mechanism doesn't touch `./runs/`'s durable-artifact storage and isn't what this ADR is about.

## Consequences

- **Positive**: `./runs/` storage becomes horizontally-scalable-ready — multiple web/worker instances can share the same S3 bucket, closing the underlying reason the Sprint 3 upload bug was possible in the first place (though that specific bug is already fixed independently, per its own scoped fix).
- **Positive**: environment-scoped key prefixing (`{environment}/{tenant_id}/...`) gives a real, cheap safety guarantee against dev/prod data collision without new infrastructure cost.
- **Positive**: `local` remains the default — no behavior change, no new required config, for local dev or if S3 is never configured.
- **Negative**: `audit/db.py`'s file I/O call sites all need touching (not a drop-in — `pandas.read_csv`/`to_csv` need to go through in-memory buffers for the S3 path rather than a plain filesystem path).
- **Negative**: new dependency (`boto3`), new required manual setup (AWS bucket + IAM credentials) before S3 mode can be used at all — `local` stays fully functional without it.
- **Neutral**: no database migration — this is a storage-layer change only, no new tables/columns.

## Related

- Vault: `bugs-solved/mypy-pytest-hang-agent-sandbox.md`-adjacent lesson, but really: `decisions/s3-write-client-boto3-vs-pyarrow.md` (sibling project, same owner) — the boto3-over-pyarrow precedent this ADR reuses directly.
- Vault: `artefact/sprint-roadmap.md` — Sprint 4 scope.
- `src/ai_etl/audit/db.py` — the module this ADR's storage abstraction integrates into.
- `docs/adr/ADR-004-sqlite-audit.md` — original audit-trail persistence pattern this extends.
