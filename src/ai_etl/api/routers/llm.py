"""`/llm` endpoints — real provider connectivity testing (Sprint 30, ADR-031).

Closes a gap ADR-014 (Sprint 23) left open: provider selection was verified only
by mocking env vars and asserting the correct LangChain class got instantiated,
never by making a live API call. Tenant-scoped auth (`get_current_tenant_id`) even
though the test doesn't touch tenant data, matching `GET /config`'s reasoning —
one auth story for the whole API rather than a public-endpoint carve-out for one
low-stakes probe.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai_etl.api.deps import get_current_tenant_id
from ai_etl.core.llm import (
    UnsupportedProviderOrModelError,
    test_provider_connectivity,
    validate_provider_and_model,
)

router = APIRouter(prefix="/llm", tags=["llm"])


class TestConnectivityRequest(BaseModel):
    provider: str
    model: str


class TestConnectivityResponse(BaseModel):
    ok: bool
    provider: str
    model: str
    latency_ms: float
    error: str | None


@router.post("/test-connectivity")
def test_connectivity(
    body: TestConnectivityRequest,
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> TestConnectivityResponse:
    """Make one real, minimal LLM call against `provider`/`model` and report
    whether it succeeded. Always returns 200 with `ok: false` and an `error`
    string on failure (missing credential, unreachable provider, invalid model
    id) — never a 5xx for "the provider itself failed", since a failed
    connectivity check is exactly the useful, expected answer this endpoint
    exists to give. Only rejects with 400 before attempting the call if
    `provider`/`model` isn't in `core.llm.ALLOWED_MODELS_BY_PROVIDER` at all —
    that's a client input error, not a provider connectivity result.
    """
    try:
        validate_provider_and_model(body.provider, body.model)
    except UnsupportedProviderOrModelError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    result = test_provider_connectivity(body.provider, body.model)
    return TestConnectivityResponse(**result)
