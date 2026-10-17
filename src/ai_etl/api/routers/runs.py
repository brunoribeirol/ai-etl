"""`/runs` endpoints — thin wrappers over `audit/db.py` and `services/execution_queue.py`
(ADR-011). No pipeline/agent logic lives here; every function this router calls
already exists and is already tested."""

import io
import uuid
from pathlib import Path
from typing import Annotated, Any, Optional, cast

import pandas as pd
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ai_etl.api.config import RUNS_DIR, UPLOADS_DIR
from ai_etl.api.deps import get_current_tenant_id, require_role
from ai_etl.api.serialization import nan_to_none_records, serialize_full_result
from ai_etl.audit.db import list_pending_approvals, load_full_result, load_history
from ai_etl.core.llm import UnsupportedProviderOrModelError, validate_provider_and_model
from ai_etl.services.execution_queue import (
    BudgetExceededError,
    RateLimitExceededError,
    enqueue_analysis,
    get_task_status,
)
from ai_etl.services.pipeline_service import reject_pending_load, resume_pending_load
from ai_etl.services.spec_builder import auto_generate_spec
from ai_etl.sources.document_source import extract_document_text

router = APIRouter(prefix="/runs", tags=["runs"])


class RejectRunRequest(BaseModel):
    """Sprint 27 (ADR-028) — optional free-text reason an operator declines a
    gated write. Never required — `reason` defaults to "" (no explanation
    given is still a valid, explicit rejection)."""

    reason: str = ""


_DOCUMENT_EXTENSIONS = (".pdf", ".docx")


def _document_spec(
    path: Path,
    output_csv: Path,
    business_question: str,
    additional_instructions: str = "",
) -> Optional[str]:
    """Spec text for a PDF/DOCX upload — the document-connector equivalent of
    `auto_generate_spec()`, whose signature needs a pre-parsed DataFrame a
    document doesn't have yet.

    Real bug found 2026-08-30, live testing: `_dataframe_from_upload()` below
    never had a `.pdf`/`.docx` branch at all — despite the upload UI
    advertising both and `sources/document_source.py` existing specifically
    to handle them — so every PDF/DOCX upload hit `create_run`'s generic
    "Could not parse uploaded file." 400 before ever reaching the pipeline.

    Deliberately does NOT call `document_source.load_document()` (which
    structures text into rows via an LLM call) — that would pay for the
    same LLM extraction twice: once here just to preview, once for real
    inside the pipeline's own Extractor -> `load_document()` call. Instead
    uses `extract_document_text()` (no LLM, deterministic) for a cheap
    validity check and a short excerpt for the spec text, and lets the real
    row-structuring happen exactly once, where it already happens.

    Returns `None` (never raises) if the document can't even be read as
    text — `create_run` then reports the same "Could not parse uploaded
    file." error as every other unreadable upload, not a different message
    for this one source type.
    """
    try:
        text = extract_document_text(str(path))
    except Exception:
        return None
    if not text.strip():
        return None

    question_hint = (
        f" The analysis should answer: {business_question}." if business_question else ""
    )
    # 2026-09-04 gap-closing fix — see `spec_builder.auto_generate_spec`'s identical
    # `additional_instructions` param and docstring for why this exists.
    user_hint = (
        f"The user also gave these specific instructions — follow them, and where "
        f"they conflict with the generic cleaning steps below, the user's instructions "
        f"win: {additional_instructions}\n"
        if additional_instructions
        else ""
    )
    preview = text[:1000]
    return (
        f"Read the document at {path}.{question_hint}\n"
        f"{user_hint}"
        f'Excerpt of its extracted text, for context:\n"""\n{preview}\n"""\n'
        f"Extract the tabular data in this document (a table, a list of records, "
        f"or structured line items) into rows. "
        f"Clean the data: remove completely duplicate rows, standardize column names "
        f"to snake_case. "
        f"Save the cleaned result to {output_csv}."
    )


_EXCEL_EXTENSIONS = (".xlsx", ".xls")


def _dataframe_from_upload(
    filename: str, contents: bytes, sheet_name: str | int | None = None
) -> Optional[pd.DataFrame]:
    """Mirrors `app.py::_read_uploaded_file`'s extension dispatch, adapted for
    already-read bytes (FastAPI's `UploadFile` is consumed once, unlike
    Streamlit's `UploadedFile`, which supports re-reading).

    `sheet_name` (Wave 1 item 2, 2026-09-05 mega-audit remediation): before this
    fix, an Excel upload always silently read `pd.read_excel(buffer)`'s default
    (sheet index 0) for this preview/schema-detection DataFrame — even when the
    workbook had other sheets, and even though the *real* pipeline execution
    (`agents/pipeline/extractor.py` -> `sources/csv_source.py::_load_excel`)
    explicitly raises rather than guess which sheet is "right" when more than
    one exists and none was named. That mismatch is the reported bug: the
    upload preview would succeed (silently on the wrong sheet, for a multi-sheet
    file), then the real run would fail with no warning the preview could have
    given. `create_run` below now resolves the sheet choice (via `/runs/excel-sheets`
    + the frontend's picker) *before* calling this, so this preview reads the
    same sheet the real pipeline will.
    """
    name = filename.lower()
    buffer = io.BytesIO(contents)
    try:
        if name.endswith(".csv"):
            return pd.read_csv(buffer)
        if name.endswith(_EXCEL_EXTENSIONS):
            return pd.read_excel(buffer, sheet_name=sheet_name if sheet_name is not None else 0)
        if name.endswith(".json"):
            return pd.read_json(buffer)
    except Exception:
        return None
    return None


def _excel_sheet_names(contents: bytes) -> list[str]:
    """All sheet names in an uploaded Excel workbook, in file order.

    Callers (`get_excel_sheets` below) are only reached for `.xlsx`/`.xls`
    filenames — errors reading a corrupt/non-Excel file propagate as a 400
    from the route, same as `_dataframe_from_upload`'s existing "Could not
    parse uploaded file." shape for other unreadable uploads.
    """
    return [str(name) for name in pd.ExcelFile(io.BytesIO(contents)).sheet_names]


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


@router.get("/pending-approval")
def get_pending_approvals(
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> list[dict[str, Any]]:
    """Sprint 27 (ADR-028) — every run of this tenant currently gated
    `awaiting_approval`, most recent first. Declared before `/{run_id}` so
    FastAPI's path matching doesn't swallow this literal path as a `run_id`.
    """
    return list_pending_approvals(tenant_id)


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


@router.post("/excel-sheets")
async def get_excel_sheets(
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
    file: UploadFile,
) -> dict[str, list[str]]:
    """Wave 1 item 2, 2026-09-05 mega-audit remediation
    (docs/archive/work/2026-09-05-mega-audit-remediation-plan.md): lets the upload form
    ask "does this workbook have more than one sheet, and what are they called"
    *before* the user submits the run, so they can pick the right one instead of
    the pipeline always defaulting to (or, before this fix, sometimes failing
    on) the first sheet. Read-only, no write/budget/rate-limit implications —
    `get_current_tenant_id` (auth-only) is the right dependency here, the same
    one `/status` uses, not `require_role("editor")`.

    Returns `{"sheets": []}` (not an error) for a non-Excel filename — the
    frontend only calls this for `.xlsx`/`.xls` uploads, but a wrong/empty
    filename shouldn't 400 a preflight check.
    """
    if not file.filename or not file.filename.lower().endswith(_EXCEL_EXTENSIONS):
        return {"sheets": []}
    contents = await file.read()
    try:
        sheets = _excel_sheet_names(contents)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Could not read the Excel workbook.") from e
    return {"sheets": sheets}


@router.post("")
async def create_run(
    tenant_id: Annotated[str, Depends(require_role("editor"))],
    business_question: Annotated[str, Form()] = "",
    manual_spec: Annotated[str, Form()] = "",
    llm_provider_override: Annotated[Optional[str], Form()] = None,
    llm_model_override: Annotated[Optional[str], Form()] = None,
    sheet_name: Annotated[Optional[str], Form()] = None,
    locale: Annotated[Optional[str], Form()] = None,
    file: Optional[UploadFile] = None,
) -> dict[str, str]:
    """Wraps `execution_queue.enqueue_analysis`. Originally mirrored `app.py::_tab_executar`'s
    upload-vs-manual-spec branching exactly (`auto_generate_spec` for an uploaded file, the
    raw textarea value otherwise) — but that branching silently discarded whatever the user
    typed in `manual_spec` whenever a file was also attached, even though the UI's "optional
    if a file was attached" label implied it would still be taken into account. Real,
    live-verified gap found 2026-09-04: typed "rename dt to date, filter active rows" was
    never seen by the Orchestrator at all when a file was uploaded alongside it. Fixed by
    threading `manual_spec` through as `additional_instructions` to both
    `auto_generate_spec`/`_document_spec` instead of dropping it — the file-derived spec is
    still primary (columns/rows/dtypes the Orchestrator needs), the user's typed text is now
    layered on top and explicitly marked as taking priority on conflict.

    `llm_provider_override`/`llm_model_override`: gap-closing fix (2026-08-25
    audit, Wave 4) — the avulso "Executar" flow's `ModelPicker` selection
    (`frontend/src/components/executar-form.tsx`), sent under the same field
    names as `PipelineState`'s. An avulso run has no `saved_pipeline_id` for
    `run_silver_pipeline` to resolve a DB-stored override from (Sprint 30,
    ADR-031 §3 only covers saved pipelines) — this is that path's equivalent.
    Both or neither must be set (same posture as `PUT /pipelines/{id}/llm-config`),
    and the pair must be in `core.llm.ALLOWED_MODELS_BY_PROVIDER`.

    `sheet_name`: Wave 1 item 2, 2026-09-05 mega-audit remediation. Only
    meaningful for a `.xlsx`/`.xls` `file` — the frontend resolves it via
    `POST /runs/excel-sheets` + a picker before submit whenever the workbook
    has more than one sheet, so the Orchestrator's generated plan (and the
    real pipeline execution behind it) reads the sheet the user actually
    meant instead of always defaulting to (or previously, failing on) the
    first one.
    """
    if (llm_provider_override is None) != (llm_model_override is None):
        raise HTTPException(
            status_code=400,
            detail="llm_provider_override and llm_model_override must be set together, "
            "or both omitted.",
        )
    if llm_provider_override is not None and llm_model_override is not None:
        try:
            validate_provider_and_model(llm_provider_override, llm_model_override)
        except UnsupportedProviderOrModelError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    file_path: Optional[str] = None
    file_bytes: Optional[bytes] = None

    if file is not None and file.filename:
        contents = await file.read()
        name = file.filename.lower()
        suffix = Path(file.filename).suffix
        saved_path = UPLOADS_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
        output_csv = RUNS_DIR / f"{uuid.uuid4()}_silver.csv"

        if name.endswith(_DOCUMENT_EXTENSIONS):
            saved_path.write_bytes(contents)
            spec = _document_spec(
                saved_path,
                output_csv,
                business_question.strip(),
                additional_instructions=manual_spec.strip(),
            )
            if spec is None:
                saved_path.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="Could not parse uploaded file.")
        else:
            resolved_sheet_name: str | int | None = sheet_name
            if name.endswith(_EXCEL_EXTENSIONS) and resolved_sheet_name is None:
                # Wave 1 item 2, 2026-09-05 mega-audit remediation: don't
                # silently default to sheet 0 for a multi-sheet workbook the
                # way `_dataframe_from_upload` used to — the frontend's
                # `/runs/excel-sheets` picker (`run-form.tsx`) is supposed to
                # have already resolved this before submit for any file with
                # more than one sheet. Landing here with `sheet_name` still
                # unset and >1 sheet means that step was skipped (an older
                # client, or the picker itself failing) — fail with a clear
                # 400 rather than guess, matching `_load_excel`'s own
                # no-guessing stance for the real pipeline execution path.
                try:
                    sheets = _excel_sheet_names(contents)
                except Exception as e:
                    raise HTTPException(
                        status_code=400, detail="Could not parse uploaded file."
                    ) from e
                if len(sheets) > 1:
                    raise HTTPException(
                        status_code=400,
                        detail=f"This workbook has {len(sheets)} sheets ({', '.join(sheets)}) "
                        "— pass sheet_name to choose one.",
                    )
            df_bronze = _dataframe_from_upload(file.filename, contents, resolved_sheet_name)
            if df_bronze is None:
                raise HTTPException(status_code=400, detail="Could not parse uploaded file.")
            saved_path.write_bytes(contents)
            spec = auto_generate_spec(
                saved_path,
                df_bronze,
                output_csv,
                business_question.strip(),
                additional_instructions=manual_spec.strip(),
                sheet_name=resolved_sheet_name,
            )

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
            llm_provider_override=llm_provider_override,
            llm_model_override=llm_model_override,
            # Audit finding H5 / D4: the UI's active locale (next-intl cookie),
            # sent by run-form.tsx, wins over the tenant's stored users.locale for
            # this run's progress messages + narrative — so an English-UI user
            # never sees Portuguese progress/narrative again.
            locale_override=locale,
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


# What `POST /runs/{run_id}/approve` answers when the write fails. The Loader's own
# error is the text of the destination driver's exception; the stored run keeps it,
# with credentials scrubbed (`audit/db/runs.py`), and `GET /runs/{run_id}` returns
# that copy. This response does not repeat the unscrubbed text.
_WRITE_FAILED_AFTER_APPROVAL = "The write failed after approval. Open the run to see the cause."


def _run_summary(state: dict[str, Any], error: str | None) -> dict[str, Any]:
    """Small JSON-safe summary of a post-approval/rejection `PipelineState` —
    mirrors `execution_queue.run_full_analysis_task`'s own return shape
    rather than the full `AnalysisRunResult` (no Gold/Science/Advisor result
    changes here, only the Loader's outcome). `error` is passed by the caller,
    which knows whether the state's own error is safe to return."""
    return {
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "error": error,
        "load_result": state.get("load_result"),
    }


@router.post("/{run_id}/approve")
def approve_run(
    run_id: str,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> dict[str, Any]:
    """Sprint 27 (ADR-028) — an operator approves a gated write: performs the
    real write now (`pipeline_service.resume_pending_load`) and returns the
    updated run's outcome. 404s for an unknown/unowned run id; 409s if the
    run exists but isn't currently `awaiting_approval` (already resolved, or
    never gated) — same "known-but-wrong-state" distinction `PATCH
    /pipelines/{id}` already makes via 404 vs. 400 for its own preconditions.
    """
    try:
        result_state = resume_pending_load(run_id, tenant_id, run_dir=str(RUNS_DIR))
    except ValueError as e:
        status_code = 404 if "not found" in str(e) else 409
        raise HTTPException(status_code=status_code, detail=str(e)) from e
    summary_state = cast("dict[str, Any]", result_state)
    write_failed = summary_state.get("status") != "completed"
    return _run_summary(summary_state, error=_WRITE_FAILED_AFTER_APPROVAL if write_failed else None)


@router.post("/{run_id}/reject")
def reject_run(
    run_id: str,
    body: RejectRunRequest,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> dict[str, Any]:
    """Sprint 27 (ADR-028) — an operator declines a gated write: never writes
    to the destination, marks the run `failed` with an explicit rejection
    reason. Same 404/409 mapping as `approve_run`.
    """
    try:
        result_state = reject_pending_load(
            run_id, tenant_id, run_dir=str(RUNS_DIR), reason=body.reason
        )
    except ValueError as e:
        status_code = 404 if "not found" in str(e) else 409
        raise HTTPException(status_code=status_code, detail=str(e)) from e
    # The error of a rejected run is the operator's own reason, not an exception's text.
    summary_state = cast("dict[str, Any]", result_state)
    return _run_summary(summary_state, error=summary_state.get("error"))
