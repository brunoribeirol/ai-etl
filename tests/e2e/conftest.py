"""Shared fixtures for end-to-end tests (Sprint 5).

Exercises the full stack together, for real: Postgres (`app-postgres-test` for
the audit trail, `postgres-test` as a pipeline source/destination), Redis-backed
Celery (`task_always_eager` — still goes through `enqueue_analysis`'s real code
path: rate limiting, `.delay()`, task registration, JSON-safe result summary —
just synchronous, so no separate worker process is needed in CI), the real
sandboxed code executor (ADR-007), and real Clerk-JWT-shaped auth verification
(fake JWKS resolving to a locally generated RSA keypair — same pattern as
`tests/unit/test_auth_service.py`, no network call to Clerk).

**Only the LLM calls are mocked** (Orchestrator/Transformer/Planner, via
`mock_pipeline_llm`) — every existing unit test in this project already avoids
real OpenAI calls (`test_orchestrator.py`, `test_transformer.py`, ...) for the
same reason: cost and network-flakiness have no place in a suite that runs on
every push. "Full stack" here means every layer *this project built*
(auth/tenancy/sandbox/async/storage), not a live OpenAI dependency.

Skipped automatically when Postgres/Redis aren't reachable, mirroring
`tests/integration/test_audit_persistence.py`'s skip convention.
"""

import json
import os
import time
import uuid
from collections.abc import Iterator
from typing import Any, Callable
from unittest.mock import MagicMock

import jwt
import pytest
import redis
import sqlalchemy
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from sqlalchemy import text

from ai_etl.audit import connection
from ai_etl.audit.db import ensure_user
from ai_etl.audit.models import metadata
from ai_etl.core.celery_app import celery_app
from ai_etl.services import auth_service

_TEST_APP_DATABASE_URL = os.getenv(
    "TEST_APP_DATABASE_URL",
    "postgresql://ai_etl_app_test:ai_etl_app_test@localhost:5435/ai_etl_app_test_db",
)
TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL", "postgresql://test:test@localhost:5433/testdb")
_TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/0")
_JWKS_URL = "https://example.invalid/.well-known/jwks.json"
_ISSUER = "https://example.invalid"


def _database_reachable(url: str) -> bool:
    try:
        engine = sqlalchemy.create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _redis_reachable() -> bool:
    try:
        redis.Redis.from_url(_TEST_REDIS_URL).ping()
        return True
    except Exception:
        return False


def _full_stack_reachable() -> bool:
    return (
        _database_reachable(_TEST_APP_DATABASE_URL)
        and _database_reachable(TEST_POSTGRES_URL)
        and _redis_reachable()
    )


requires_full_stack = pytest.mark.skipif(
    not _full_stack_reachable(),
    reason=(
        "app-postgres-test, postgres-test and/or redis not reachable; run "
        "`make app-db-test-up` (or `docker compose up -d app-postgres-test "
        "postgres-test redis`) to enable end-to-end tests"
    ),
)


@pytest.fixture(autouse=True)
def _app_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_DATABASE_URL", _TEST_APP_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", _TEST_REDIS_URL)
    connection.get_engine.cache_clear()
    engine = connection.get_engine()
    metadata.create_all(engine)
    yield
    with engine.begin() as conn:
        for table in reversed(metadata.sorted_tables):
            conn.execute(table.delete())
    connection.get_engine.cache_clear()


@pytest.fixture(autouse=True)
def _celery_eager() -> Iterator[None]:
    """Runs Celery tasks synchronously in-process, still through the real
    `.delay()`/task-registration/JSON-serialization round-trip — exercises the
    async plumbing without a separate worker process in CI."""
    original_eager = celery_app.conf.task_always_eager
    original_propagates = celery_app.conf.task_eager_propagates
    original_store_eager = celery_app.conf.task_store_eager_result
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    # Without this, a fresh AsyncResult(task_id, ...) built by get_task_status
    # (a separate call from enqueue_analysis's own `.delay()`) can't find the
    # eagerly-computed result in the backend — it would read back PENDING
    # forever, even though the task already ran synchronously above.
    celery_app.conf.task_store_eager_result = True
    yield
    celery_app.conf.task_always_eager = original_eager
    celery_app.conf.task_eager_propagates = original_propagates
    celery_app.conf.task_store_eager_result = original_store_eager


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_service, "_jwks_client", None)
    monkeypatch.setattr(auth_service, "_jwks_client_url", None)
    monkeypatch.setenv("CLERK_JWKS_URL", _JWKS_URL)
    monkeypatch.setenv("CLERK_ISSUER", _ISSUER)


@pytest.fixture
def rsa_keypair() -> tuple[RSAPrivateKey, RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


class _FakeSigningKey:
    """Stand-in for `jwt.PyJWK` — `auth_service` only ever reads `.key`."""

    def __init__(self, key: Any) -> None:
        self.key = key


@pytest.fixture
def test_tenant(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> dict[str, str]:
    """A real, verifiable Clerk-shaped session token plus a matching `users` row —
    mirrors what `app.py`'s sign-in gate hands the Executar/Histórico tabs after a
    real login, minted with no network call to Clerk (fake JWKS, same pattern as
    `tests/unit/test_auth_service.py`)."""
    private_key, public_key = rsa_keypair

    class _FakeJWKSClient:
        def __init__(self, url: str) -> None:
            self.url = url

        def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
            return _FakeSigningKey(public_key)

    monkeypatch.setattr(auth_service, "PyJWKClient", _FakeJWKSClient)

    tenant_id = f"user_e2e_{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    payload = {"sub": tenant_id, "iss": _ISSUER, "iat": now, "exp": now + 3600}
    token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "e2e-kid"})

    ensure_user(tenant_id)
    verified = auth_service.verify_session_token(token)
    assert verified["ok"], f"test token failed verification: {verified['error']}"

    return {"token": token, "tenant_id": tenant_id}


@pytest.fixture
def mock_pipeline_llm(mocker) -> Callable[[dict, str], None]:
    """Patches every LLM call site `run_full_analysis` touches, so e2e
    scenarios exercise the real pipeline/sandbox/async/auth plumbing without a
    real OpenAI call. Planner is always mocked to return zero sub-tasks — these
    scenarios are Silver-ETL only, no business question — but `run_full_analysis`
    calls Advisor unconditionally whenever Silver produces a non-empty
    DataFrame, regardless of how many (if any) Gold/Science sub-tasks ran, so
    Advisor's LLM call is mocked too, or these scenarios would hit a real
    (credential-less, in CI) OpenAI call and fail with OpenAIError.

    Returns `configure(plan: dict, transform_code: str) -> None`.
    """

    _zero_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def _mock_response(content: str) -> MagicMock:
        # `extract_token_usage` (core/llm.py) reads `.usage_metadata` off the
        # response — a bare MagicMock auto-vivifies that attribute into
        # another (truthy, non-dict) MagicMock instead of leaving it unset,
        # which breaks `int(usage.get(...))` downstream. Must be set
        # explicitly, same pattern tests/unit/test_planner.py already uses.
        response = MagicMock(content=content)
        response.usage_metadata = dict(_zero_usage)
        return response

    def configure(plan: dict, transform_code: str) -> None:
        orchestrator_llm = MagicMock()
        orchestrator_llm.invoke.return_value = _mock_response(json.dumps(plan))
        mocker.patch("ai_etl.agents.orchestrator.get_llm", return_value=orchestrator_llm)

        transformer_llm = MagicMock()
        transformer_llm.invoke.return_value = _mock_response(transform_code)
        mocker.patch("ai_etl.agents.transformer.get_llm", return_value=transformer_llm)

        planner_llm = MagicMock()
        planner_llm.invoke.return_value = _mock_response("[]")
        mocker.patch("ai_etl.agents.planner.get_llm", return_value=planner_llm)

        advisor_llm = MagicMock()
        advisor_llm.invoke.return_value = _mock_response(
            json.dumps({"recommendations": [], "summary": "No sub-tasks were run."})
        )
        mocker.patch("ai_etl.agents.advisor.get_llm", return_value=advisor_llm)

    return configure


@pytest.fixture
def postgres_customers_table(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Seeds `public.customers` in the pipeline source/destination test Postgres
    (`postgres-test`, port 5433) — a small, fixed fixture standing in for
    `case_study/data/seed_postgres.py`'s 400-row seed, scoped down for a fast
    e2e run. Cleaned up after the test."""
    monkeypatch.setenv("POSTGRES_URL", TEST_POSTGRES_URL)
    engine = sqlalchemy.create_engine(TEST_POSTGRES_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS public.customers CASCADE"))
        conn.execute(
            text(
                "CREATE TABLE public.customers "
                "(customer_id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                "city TEXT NOT NULL, email TEXT NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO public.customers (customer_id, name, city, email) VALUES "
                "(100, 'Ana Silva', 'Recife', 'customer100@example.com'), "
                "(200, 'Bruno Santos', 'Olinda', 'customer200@example.com'), "
                "(300, 'Carla Souza', 'Recife', 'customer300@example.com'), "
                "(400, 'Daniela Costa', 'Recife', 'customer400@example.com'), "
                "(500, 'Eduardo Lima', 'Recife', 'customer500@example.com'), "
                "(600, 'Fernanda Alves', 'Recife', 'customer600@example.com')"
            )
        )
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS public.customers CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS public.orders_cleaned CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS public.enriched_orders CASCADE"))
    engine.dispose()
