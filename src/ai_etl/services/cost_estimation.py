"""Pre-run cost estimation (Sprint 35, FinOps).

`estimate_run_cost` blends two signals into a single estimate:

1. `heuristic_cost_usd` — `core.cost_estimation`'s token heuristic
   (row/column-count based), priced via `core.pricing.compute_cost_usd` for
   the requested provider/model. Size- and model-aware, but a first-pass
   calibration (see `core/cost_estimation.py`'s module docstring).
2. `historical_avg_cost_usd` — the tenant's own average cost per run
   (`analysis_runs.cost_usd`, most recent `TENANT_HISTORY_RUN_LIMIT` priced
   runs), falling back to a global average across all tenants when this
   tenant has no priced run history yet (a brand-new tenant, or one whose
   only runs so far were Ollama's genuine $0 and therefore excluded — see
   `get_avg_run_cost_usd`). Model/size-agnostic, but grounded in real spend.

`estimated_cost_usd` is a fixed-weight blend of the two (`HEURISTIC_WEIGHT`)
whenever a historical signal is available; heuristic-only otherwise (a fresh
deployment with zero priced runs anywhere). The weight is a deliberately
simple starting point, not a fitted regression — this sprint's own
"definição de pronto" calls for validating the resulting margin against real
executions *after* shipping, not calibrating it before.

No new table/migration: nothing here is persisted — every call recomputes
the estimate from `MODEL_PRICING_USD_PER_MILLION_TOKENS` and the existing
`analysis_runs` history. See ADR note in the Sprint 35 PR description for why
this sprint didn't open a new ADR (no real architectural trade-off — this is
an additive read-only heuristic, not a change to how anything is stored or
enforced).
"""

from typing import Literal, TypedDict

from ai_etl.audit.db.budget import get_avg_run_cost_usd, get_global_avg_run_cost_usd
from ai_etl.core.cost_estimation import estimate_token_usage
from ai_etl.core.llm import validate_provider_and_model
from ai_etl.core.pricing import compute_cost_usd

# How much weight the size/model-aware heuristic gets vs. the tenant's (or
# the deployment's) historical average once both are available.
HEURISTIC_WEIGHT = 0.6

# How many of the tenant's most recent priced runs feed the tenant-history
# signal, and how many of the deployment's most recent priced runs (across
# every tenant) feed the global fallback. Same order of magnitude as other
# "recent N" windows already used in this project (e.g.
# `audit/db/health.py::DEFAULT_PIPELINE_HISTORY_LIMIT`).
TENANT_HISTORY_RUN_LIMIT = 20
GLOBAL_HISTORY_RUN_LIMIT = 200

CostEstimateBasis = Literal[
    "heuristic_only",
    "heuristic_and_tenant_history",
    "heuristic_and_global_history",
]


class CostEstimate(TypedDict):
    """Return shape of `estimate_run_cost` — also `POST /runs/estimate`'s
    response body. `basis` tells the caller which historical signal (if any)
    was blended in, so a frontend can label a cold-start estimate
    differently (e.g. "heuristic_only" — no run history yet for this
    deployment — vs. a tenant-calibrated one)."""

    estimated_cost_usd: float
    provider: str
    model: str
    basis: CostEstimateBasis
    heuristic_cost_usd: float
    historical_avg_cost_usd: float | None
    estimated_input_tokens: int
    estimated_output_tokens: int


def estimate_run_cost(
    tenant_id: str,
    row_count: int,
    col_count: int,
    provider: str,
    model: str,
) -> CostEstimate:
    """Estimate the USD cost of a run against a dataset shaped
    `row_count` x `col_count`, for `provider`/`model`.

    Raises `core.llm.UnsupportedProviderOrModelError` for a provider/model
    outside `core.llm.ALLOWED_MODELS_BY_PROVIDER` — same validation
    `PUT /pipelines/{id}/llm-config` already applies (Sprint 30, ADR-031), so
    an estimate is never produced for a selection the tenant could not
    actually run. Raises `ValueError` for a negative `row_count`/`col_count`
    (see `core.cost_estimation.estimate_token_usage`)."""
    validate_provider_and_model(provider, model)

    tokens = estimate_token_usage(row_count, col_count)
    # `compute_cost_usd` returns `None` only for a model missing a pricing
    # entry — every model in `ALLOWED_MODELS_BY_PROVIDER` has one (enforced by
    # `test_every_llm_allowed_model_has_a_pricing_entry`), so `model` having
    # already passed `validate_provider_and_model` above means this is never
    # actually `None` in practice; `or 0.0` is defensive, not expected to fire.
    heuristic_cost = compute_cost_usd(model, tokens) or 0.0

    tenant_avg = get_avg_run_cost_usd(tenant_id, limit=TENANT_HISTORY_RUN_LIMIT)
    basis: CostEstimateBasis
    historical: float | None
    if tenant_avg is not None:
        basis, historical = "heuristic_and_tenant_history", tenant_avg
    else:
        global_avg = get_global_avg_run_cost_usd(limit=GLOBAL_HISTORY_RUN_LIMIT)
        if global_avg is not None:
            basis, historical = "heuristic_and_global_history", global_avg
        else:
            basis, historical = "heuristic_only", None

    estimated_cost = (
        heuristic_cost
        if historical is None
        else round(HEURISTIC_WEIGHT * heuristic_cost + (1 - HEURISTIC_WEIGHT) * historical, 6)
    )

    return CostEstimate(
        estimated_cost_usd=estimated_cost,
        provider=provider,
        model=model,
        basis=basis,
        heuristic_cost_usd=heuristic_cost,
        historical_avg_cost_usd=historical,
        estimated_input_tokens=tokens["input_tokens"],
        estimated_output_tokens=tokens["output_tokens"],
    )
