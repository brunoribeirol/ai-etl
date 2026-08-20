"""`/runs` endpoints — thin wrappers over `audit/db.py` and `services/execution_queue.py`
(ADR-011). No pipeline/agent logic lives here; every function this router calls
already exists and is already tested."""

import io
import uuid
from pathlib import Path
from typing import Annotated, Any, Optional, cast

import pandas as pd
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from ai_etl.api.config import RUNS_DIR, UPLOADS_DIR
from ai_etl.api.deps import get_current_tenant_id
from ai_etl.api.serialization import nan_to_none_records, serialize_full_result
from ai_etl.audit.db import load_full_result, load_history
from ai_etl.services.execution_queue import (
    BudgetExceededError,
    RateLimitExceededError,
    enqueue_analysis,
    get_task_status,
)
from ai_etl.services.spec_builder import auto_generate_spec

router = APIRouter(prefix="/runs", tags=["runs"])


def _dataframe_from_upload(filename: str, contents: bytes) -> Optional[pd.DataFrame]:
    """Mirrors `app.py::_read_uploaded_file`'s extension dispatch, adapted for
    already-read bytes (FastAPI's `UploadFile` is consumed once, unlike
    Streamlit's `UploadedFile`, which supports re-reading)."""
    name = filename.lower()
    buffer = io.BytesIO(contents)
    try:
        if name.endswith(".csv"):
            return pd.read_csv(buffer)
        if name.endswith((".xlsx", ".xls")):
            return pd.read_excel(buffer)
        if name.endswith(".json"):
            return pd.read_json(buffer)
    except Exception:
        return None
    return None


@router.get("")
def list_runs(
    limit: int = 20,
    tenant_id: str = Depends(get_current_tenant_id),
) -> list[dict[str, Any]]:
    """Wraps `audit.db.load_history` — same tenant-scoped query the Streamlit
    History tab uses."""
    history = load_history(limit=limit, tenant_id=tenant_id)
    records = cast("list[dict[str, Any]]", history.to_dict(orient="records"))
    return nan_to_none_records(records)


@router.get("/{run_id}")
def get_run(
    run_id: str,
    tenant_id: str = Depends(get_current_tenant_id),
) -> dict[str, Any]:
    """Wraps `audit.db.load_full_result` — 404 covers both "unknown run_id"
    and "belongs to another tenant" (indistinguishable by design, same as
    `load_full_result`'s own soft-fail shape — see its docstring)."""
    result = load_full_result(run_id, log_dir=str(RUNS_DIR), tenant_id=tenant_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return serialize_full_result(result)


@router.get("/{task_id}/status")
def get_status(
    task_id: str,
    tenant_id: str = Depends(
        get_current_tenant_id
    ),  # auth-only, no ownership check on task_id itself
) -> dict[str, Any]:
    """Wraps `execution_queue.get_task_status`. Celery task ids aren't tenant-scoped
    (same as the Streamlit polling loop, which only ever polls a task id it just
    enqueued itself) — this endpoint requires *a* valid session, not proof the
    caller owns this specific task_id, matching pre-existing behavior."""
    return get_task_status(task_id)


@router.post("")
async def create_run(
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
    business_question: Annotated[str, Form()] = "",
    manual_spec: Annotated[str, Form()] = "",
    file: Optional[UploadFile] = None,
) -> dict[str, str]:
    """Wraps `execution_queue.enqueue_analysis` — mirrors `app.py::_tab_executar`'s
    upload-vs-manual-spec branching exactly (`auto_generate_spec` for an
    uploaded file, the raw textarea value otherwise)."""
    file_path: Optional[str] = None
    file_bytes: Optional[bytes] = None

    if file is not None and file.filename:
        contents = await file.read()
        df_bronze = _dataframe_from_upload(file.filename, contents)
        if df_bronze is None:
            raise HTTPException(status_code=400, detail="Could not parse uploaded file.")

        suffix = Path(file.filename).suffix
        saved_path = UPLOADS_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
        saved_path.write_bytes(contents)
        output_csv = RUNS_DIR / f"{uuid.uuid4()}_silver.csv"
        spec = auto_generate_spec(saved_path, df_bronze, output_csv, business_question.strip())
        file_path = str(saved_path)
        file_bytes = contents
    elif manual_spec.strip():
        spec = manual_spec.strip()
    else:
        raise HTTPException(status_code=400, detail="Provide either a file or manual_spec.")

    try:
        task_id = enqueue_analysis(
            spec,
            business_question,
            run_dir=str(RUNS_DIR),
            tenant_id=tenant_id,
            file_path=file_path,
            file_bytes=file_bytes,
        )
    except RateLimitExceededError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except BudgetExceededError as e:
        # 402 Payment Required — semantically distinct from 429 (rate limit):
        # this rejection is about accumulated spend, not request cadence, and
        # doesn't self-resolve after a window; it needs the cap raised or the
        # next billing period, not just "wait and retry" (ADR-019).
        raise HTTPException(status_code=402, detail=str(e)) from e

    return {"task_id": task_id}
