"""HTTP API layer (Sprint 6, ADR-011) — the Next.js frontend's backend.

A thin FastAPI wrapper over `services/pipeline_service.py`,
`services/execution_queue.py`, and `audit/db.py`, none of which change here.
Auth reuses `services/auth_service.verify_session_token` unchanged (see
`api/deps.py`) — no new authentication mechanism, no relaxed verification.
"""
