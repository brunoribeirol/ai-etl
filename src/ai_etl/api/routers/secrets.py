"""`/secrets` endpoints — tenant-scoped external-source credential storage
(Sprint 19, ADR-021). Editor-only end to end: a `viewer` cannot read, list,
or write another member's credentials, including the names alone — closing
the RBAC DoD's isolation requirement for the secrets management surface.

No endpoint here ever returns a decrypted value. `GET` returns names only
(`services/secrets_service.list_secret_names`); nothing in this router calls
`secrets_service.get_secret`, which is reserved for a future pipeline
connector to call server-side (see ADR-021 Decision 4 for why that wiring is
explicitly deferred).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ai_etl.api.deps import require_role
from ai_etl.services.secrets_service import (
    SecretsEncryptionKeyMissingError,
    delete_secret,
    list_secret_names,
    set_secret,
)

router = APIRouter(prefix="/secrets", tags=["secrets"])


class SetSecretRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1)


@router.get("")
def list_secrets(
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> list[str]:
    return list_secret_names(tenant_id)


@router.post("")
def create_or_rotate_secret(
    body: SetSecretRequest,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> dict[str, Any]:
    try:
        set_secret(tenant_id, body.name, body.value)
    except SecretsEncryptionKeyMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"name": body.name, "status": "stored"}


@router.delete("/{name}")
def remove_secret(
    name: str,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> dict[str, Any]:
    deleted = delete_secret(tenant_id, name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Secret not found.")
    return {"name": name, "status": "deleted"}
