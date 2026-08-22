"""Tenant-scoped secrets management for external source credentials (Sprint 19, ADR-022).

Replaces the process-wide-shared-env-var pattern every source connector uses
today (`POSTGRES_URL`, REST `auth.env_var`) with per-tenant, encrypted-at-rest
storage: each tenant stores its own named secret (e.g. an API key), never a
literal value another tenant's code path can read.

Encryption: Fernet (symmetric AEAD, from the `cryptography` package — already
a locked transitive dependency of `pyjwt[crypto]`, see `pyproject.toml`), key
from `AI_ETL_SECRETS_ENCRYPTION_KEY`. This module never logs a decrypted
value — only `name` and outcome are ever passed to `logger` calls, matching
the "API keys em logs" non-negotiable rule the same way the logger's
automatic redaction already covers everything else.

ADR-022 Decision 4 scopes this sprint to storage/API only — no source
connector consumes a stored secret yet (see the ADR for why: `tenant_id` is
not available inside a LangGraph node without breaking the node-signature
contract or widening `PipelineState`, both deliberately avoided here).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ai_etl.audit.connection import get_engine
from ai_etl.audit.models import tenant_secrets

logger = logging.getLogger(__name__)


class SecretsEncryptionKeyMissingError(RuntimeError):
    """Raised when `AI_ETL_SECRETS_ENCRYPTION_KEY` is not configured.

    Fails closed: this module never falls back to storing a secret in
    plaintext when the key is missing.
    """


class SecretNotFoundError(KeyError):
    """Raised by `get_secret` when no secret with that name exists for the tenant."""


def _get_fernet() -> Fernet:
    key = os.getenv("AI_ETL_SECRETS_ENCRYPTION_KEY")
    if not key:
        raise SecretsEncryptionKeyMissingError(
            "AI_ETL_SECRETS_ENCRYPTION_KEY environment variable is not set."
        )
    return Fernet(key.encode("utf-8"))


def set_secret(tenant_id: str, name: str, value: str) -> None:
    """Encrypt and upsert one named secret for a tenant.

    Upsert on `(tenant_id, name)` — calling this again with the same name
    rotates the stored value (e.g. an API key rotation) without needing a
    separate update endpoint.
    """
    ciphertext = _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    now = datetime.now(tz=timezone.utc)
    stmt = (
        pg_insert(tenant_secrets)
        .values(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            name=name,
            ciphertext=ciphertext,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[tenant_secrets.c.tenant_id, tenant_secrets.c.name],
            set_={"ciphertext": ciphertext, "updated_at": now},
        )
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)
    logger.info("Secret '%s' stored for tenant.", name)  # never logs `value`


def get_secret(tenant_id: str, name: str) -> str:
    """Decrypt and return one named secret for a tenant.

    Raises `SecretNotFoundError` if no such secret exists for this tenant —
    intentionally never returns another tenant's secret even if `name`
    collides, since the lookup is always scoped by `tenant_id`.
    """
    stmt = select(tenant_secrets.c.ciphertext).where(
        tenant_secrets.c.tenant_id == tenant_id, tenant_secrets.c.name == name
    )
    with get_engine().connect() as conn:
        row = conn.execute(stmt).fetchone()
    if row is None:
        raise SecretNotFoundError(name)
    try:
        return _get_fernet().decrypt(row.ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        # Wrong/rotated encryption key, or corrupted ciphertext — never
        # surface the raw ciphertext or key material in the error.
        raise SecretNotFoundError(name) from exc


def list_secret_names(tenant_id: str) -> list[str]:
    """Return only the names of a tenant's stored secrets — never values."""
    stmt = (
        select(tenant_secrets.c.name)
        .where(tenant_secrets.c.tenant_id == tenant_id)
        .order_by(tenant_secrets.c.name)
    )
    with get_engine().connect() as conn:
        return [row.name for row in conn.execute(stmt)]


def delete_secret(tenant_id: str, name: str) -> bool:
    """Delete one named secret for a tenant. Returns whether a row was deleted."""
    stmt = delete(tenant_secrets).where(
        tenant_secrets.c.tenant_id == tenant_id, tenant_secrets.c.name == name
    )
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
    deleted = result.rowcount > 0
    if deleted:
        logger.info("Secret '%s' deleted for tenant.", name)
    return deleted


def generate_encryption_key() -> str:
    """Generate a new Fernet key — operational helper for provisioning
    `AI_ETL_SECRETS_ENCRYPTION_KEY` once, not called by application code."""
    return Fernet.generate_key().decode("utf-8")


__all__ = [
    "SecretNotFoundError",
    "SecretsEncryptionKeyMissingError",
    "set_secret",
    "get_secret",
    "list_secret_names",
    "delete_secret",
    "generate_encryption_key",
]
