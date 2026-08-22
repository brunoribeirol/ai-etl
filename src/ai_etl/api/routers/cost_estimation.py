"""`POST /runs/estimate` — pre-run cost estimation (Sprint 35, FinOps).

Design decision: this endpoint accepts `row_count`/`col_count` directly
rather than requiring a completed upload/extraction first. A tenant
evaluating ROI wants an estimate *before* committing to a run — re-running
`agents/pipeline/extractor.py::extractor_node` just to get a shape would
defeat that. The caller is expected to already know (or cheaply compute
client-side, e.g. a CSV row/column count, or a source's known table
dimensions) the shape of the dataset it intends to run against; this
endpoint does no I/O against the tenant's actual source.

Frontend contract (frontend work is out of scope for this sprint, same
backend-first pattern as prior sprints — documented here for whoever
consumes this next): call `POST /runs/estimate` with the dataset shape and
the provider/model the run will use (or omit both to estimate against this
deployment's configured default, `core.llm.get_provider()`/`get_model_name()`)
before showing the "confirm execution" control; render `estimated_cost_usd`
(and, for transparency, `basis` — e.g. label a `"heuristic_only"` estimate as
lower-confidence than a `"heuristic_and_tenant_history"` one).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ai_etl.api.deps import get_current_tenant_id
from ai_etl.core.llm import UnsupportedProviderOrModelError, get_model_name, get_provider
from ai_etl.services.cost_estimation import CostEstimate, estimate_run_cost

router = APIRouter(prefix="/runs", tags=["runs"])


class EstimateCostRequest(BaseModel):
    row_count: int = Field(ge=0)
    col_count: int = Field(ge=0)
    # Both optional — default to this deployment's configured provider/model
    # (`core.llm.get_provider()`/`get_model_name()`), the same default a run
    # actually uses when a `saved_pipeline` has no per-pipeline override
    # (Sprint 30, ADR-031).
    provider: str | None = None
    model: str | None = None


@router.post("/estimate")
def estimate_cost(
    body: EstimateCostRequest,
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> CostEstimate:
    """Estimate the USD cost of running a pipeline against a dataset of the
    given shape, before the tenant commits to running it. Never touches the
    tenant's actual data or source — see module docstring for why."""
    provider = body.provider or get_provider()
    model = body.model or get_model_name()
    try:
        return estimate_run_cost(tenant_id, body.row_count, body.col_count, provider, model)
    except UnsupportedProviderOrModelError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
