"""Unit tests for audit persistence (db.py) — JSON-file behavior only.

Row-level Postgres behavior (upsert semantics, token aggregation, history
ordering) is covered by tests/integration/test_audit_persistence.py against a
live application database, since it isn't meaningful to fake with a mock
without duplicating the SQL. Here, `_write_run_row`/`_write_analysis_row` are
monkeypatched to no-ops so these tests exercise only the JSON-writing path
and don't require a database.

The `load_history(tenant_id=...)` tests below are the one exception: the
`tenant_id` filter is a `WHERE` clause on the *read* path, not an
upsert/aggregation detail, and its correctness (does it actually scope rows
per-session) is the whole point of the Sprint A fix — worth covering here
without waiting on a live Postgres. They run `db.load_history` against a
real (if lightweight) in-memory SQLite engine rather than mocking `get_engine`,
so the actual SQLAlchemy Core `select(...).where(...)` executes for real.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
import sqlalchemy
from sqlalchemy import Engine

from ai_etl.audit import db
from ai_etl.audit.db import _write_run_row as _real_write_run_row
from ai_etl.audit.db import save_analysis, save_run
from ai_etl.audit.models import metadata as audit_metadata
from ai_etl.audit.models import runs as runs_table
from ai_etl.audit.models import users as users_table
from ai_etl.core.state import initial_state

_ZERO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_write_run_row", lambda state, tenant_id=None: None)
    monkeypatch.setattr(db, "_write_analysis_row", lambda *args: None)


def _make_completed_state() -> dict:
    state = initial_state(spec="load sales.csv", run_id="run-abc-123")
    return {
        **state,
        "status": "completed",
        "load_result": {
            "rows_loaded": 42,
            "destination": "output.csv",
            "timestamp": "2026-06-22T00:00:00+00:00",
        },
    }


def _make_failed_state() -> dict:
    state = initial_state(spec="bad spec", run_id="run-failed-1")
    return {**state, "status": "failed", "error": "LLM failed"}


def test_save_run_creates_json_file(tmp_path: Path) -> None:
    state = _make_completed_state()
    json_path = save_run(state, log_dir=str(tmp_path))

    assert json_path.exists()
    assert json_path.suffix == ".json"
    data = json.loads(json_path.read_text())
    assert data["run_id"] == "run-abc-123"
    assert data["status"] == "completed"


def test_save_run_writes_row_with_correct_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    monkeypatch.setattr(db, "_write_run_row", lambda state, tenant_id=None: captured.update(state))

    state = _make_completed_state()
    save_run(state, log_dir=str(tmp_path))

    assert captured["run_id"] == "run-abc-123"
    assert captured["status"] == "completed"
    assert captured["load_result"]["rows_loaded"] == 42


def test_save_run_failed_state_has_no_load_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    monkeypatch.setattr(db, "_write_run_row", lambda state, tenant_id=None: captured.update(state))

    state = _make_failed_state()
    save_run(state, log_dir=str(tmp_path))

    assert captured.get("load_result") is None


def test_save_run_json_serializes_dataframe(tmp_path: Path) -> None:
    state = _make_completed_state()
    state["transformed_data"] = pd.DataFrame({"a": [1, 2]})
    json_path = save_run(state, log_dir=str(tmp_path))

    data = json.loads(json_path.read_text())
    assert "<DataFrame" in data["transformed_data"]


def test_save_run_creates_log_dir_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested" / "runs"
    state = _make_completed_state()
    save_run(state, log_dir=str(nested))
    assert nested.exists()


def test_save_run_saves_transform_code_as_py(tmp_path: Path) -> None:
    state = _make_completed_state()
    state["transformation_code"] = "def transform(dfs):\n    return dfs['x']\n"
    save_run(state, log_dir=str(tmp_path))

    py_path = tmp_path / "run-abc-123_transform.py"
    assert py_path.exists()
    assert "def transform" in py_path.read_text()


def test_save_run_json_includes_transform_code_path(tmp_path: Path) -> None:
    state = _make_completed_state()
    state["transformation_code"] = "def transform(dfs):\n    return dfs['x']\n"
    json_path = save_run(state, log_dir=str(tmp_path))

    data = json.loads(json_path.read_text())
    assert "transform_code_path" in data
    assert data["transform_code_path"].endswith("_transform.py")


def test_save_run_no_py_file_when_no_transform_code(tmp_path: Path) -> None:
    state = _make_completed_state()
    state["transformation_code"] = ""
    save_run(state, log_dir=str(tmp_path))

    py_files = list(tmp_path.glob("*_transform.py"))
    assert py_files == []


# ---------------------------------------------------------------------------
# save_analysis
# ---------------------------------------------------------------------------


def _make_gold_result(question: str = "Quais os KPIs?") -> dict:
    return {
        "task_question": question,
        "gold_df": pd.DataFrame({"product": ["A", "B"], "total": [10, 20]}),
        "fig": None,
        "narrative": "Produto B lidera.",
        "code": "gold_df = df",
        "attempts": 1,
        "error": None,
        "tokens": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    }


def _make_science_result(question: str = "Qual a previsão?") -> dict:
    return {
        "task_question": question,
        "predictions_df": pd.DataFrame({"month": ["Jan"], "predicted": [42.0]}),
        "fig": None,
        "narrative": "Tendência estável.",
        "model_info": {"model_type": "LinearRegression", "task": "regression"},
        "code": "predictions_df = df",
        "attempts": 1,
        "error": None,
        "tokens": {"input_tokens": 200, "output_tokens": 80, "total_tokens": 280},
    }


def _make_advisor_result() -> dict:
    return {
        "recommendations": [
            {
                "action": "Investir no produto B",
                "rationale": "Lidera em receita.",
                "priority": "high",
                "expected_impact": "10% de crescimento",
            }
        ],
        "summary": "Foco no produto B.",
        "error": None,
        "tokens": {"input_tokens": 50, "output_tokens": 20, "total_tokens": 70},
    }


def test_save_analysis_creates_json_file(tmp_path: Path) -> None:
    json_path = save_analysis(
        "run-analysis-1",
        [_make_gold_result()],
        [_make_science_result()],
        _make_advisor_result(),
        _ZERO_TOKENS,
        log_dir=str(tmp_path),
    )

    assert json_path.exists()
    data = json.loads(json_path.read_text())
    assert data["run_id"] == "run-analysis-1"
    assert len(data["gold"]) == 1
    assert data["gold"][0]["task_question"] == "Quais os KPIs?"
    assert data["gold"][0]["narrative"] == "Produto B lidera."
    assert len(data["science"]) == 1
    assert data["advisor"]["summary"] == "Foco no produto B."


def test_save_analysis_includes_data_preview(tmp_path: Path) -> None:
    json_path = save_analysis(
        "run-analysis-2",
        [_make_gold_result()],
        [],
        _make_advisor_result(),
        _ZERO_TOKENS,
        log_dir=str(tmp_path),
    )

    data = json.loads(json_path.read_text())
    assert data["gold"][0]["data_shape"] == [2, 2]
    assert len(data["gold"][0]["data_preview"]) == 2


def test_save_analysis_aggregates_tokens_before_writing_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_write(
        run_id: str, n_gold: int, n_science: int, tokens: dict, tenant_id: str | None = None
    ) -> None:
        captured.update(
            run_id=run_id, n_gold=n_gold, n_science=n_science, tokens=tokens, tenant_id=tenant_id
        )

    monkeypatch.setattr(db, "_write_analysis_row", fake_write)

    planner_tokens = {"input_tokens": 30, "output_tokens": 10, "total_tokens": 40}
    save_analysis(
        "run-analysis-3",
        [_make_gold_result()],
        [_make_science_result()],
        _make_advisor_result(),
        planner_tokens,
        log_dir=str(tmp_path),
    )

    assert captured["n_gold"] == 1
    assert captured["n_science"] == 1
    # 100 (gold) + 200 (science) + 50 (advisor) + 30 (planner) = 380
    assert captured["tokens"]["input_tokens"] == 380
    assert captured["tokens"]["output_tokens"] == 50 + 80 + 20 + 10
    assert captured["tokens"]["total_tokens"] == 150 + 280 + 70 + 40


def test_save_analysis_handles_empty_results(tmp_path: Path) -> None:
    json_path = save_analysis(
        "run-analysis-4", [], [], _make_advisor_result(), _ZERO_TOKENS, log_dir=str(tmp_path)
    )

    data = json.loads(json_path.read_text())
    assert data["gold"] == []
    assert data["science"] == []


def test_save_analysis_marks_repaired_subtasks(tmp_path: Path) -> None:
    repaired_gold = {**_make_gold_result(), "repaired": True}
    json_path = save_analysis(
        "run-analysis-5",
        [repaired_gold],
        [],
        _make_advisor_result(),
        _ZERO_TOKENS,
        log_dir=str(tmp_path),
    )

    data = json.loads(json_path.read_text())
    assert data["gold"][0]["repaired"] is True


# ---------------------------------------------------------------------------
# load_history(tenant_id=...)
# ---------------------------------------------------------------------------


def _make_sqlite_engine() -> Engine:
    """A fresh in-memory SQLite engine with the `runs`/`analysis_runs` schema.

    `load_history` only ever runs a plain `select(...).where(...)` against
    `runs` (no `pg_insert`/`ON CONFLICT`), so it's portable to SQLite — this
    lets the tenant-filter `WHERE` clause execute for real instead of being
    mocked, without requiring a live Postgres.
    """
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    audit_metadata.create_all(engine)
    return engine


def _insert_run_row(
    engine: Engine,
    run_id: str,
    tenant_id: str | None,
    timestamp: datetime | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            runs_table.insert().values(
                run_id=run_id,
                spec="load sales.csv",
                status="completed",
                error=None,
                rows_loaded=1,
                timestamp=timestamp or datetime.now(tz=timezone.utc),
                tenant_id=tenant_id,
            )
        )


def test_load_history_filters_by_tenant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _make_sqlite_engine()
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    _insert_run_row(engine, "run-tenant-a", tenant_id="tenant-a")
    _insert_run_row(engine, "run-tenant-b", tenant_id="tenant-b")

    history = db.load_history(tenant_id="tenant-a")

    assert list(history["run_id"]) == ["run-tenant-a"]


def test_load_history_without_tenant_id_returns_all_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _make_sqlite_engine()
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    _insert_run_row(engine, "run-tenant-a", tenant_id="tenant-a")
    _insert_run_row(engine, "run-tenant-b", tenant_id="tenant-b")

    history = db.load_history()

    assert set(history["run_id"]) == {"run-tenant-a", "run-tenant-b"}


def test_runs_table_rejects_null_tenant_id() -> None:
    """Regression guard for ADR-006 migration 0003: `runs.tenant_id` is NOT NULL
    now (it maps to a real Clerk account via a FK to `users`, not the ADR-005
    session-UUID stopgap, which allowed NULL). This replaces the old
    `test_load_history_handles_legacy_rows_with_null_tenant_id` test, whose
    premise (a legacy NULL-tenant row) the migration makes impossible — this
    proves the constraint is actually enforced by the schema, not just
    documented in the migration."""
    engine = _make_sqlite_engine()

    with pytest.raises(sqlalchemy.exc.IntegrityError):
        _insert_run_row(engine, "run-no-tenant", tenant_id=None)


# ---------------------------------------------------------------------------
# Tenant isolation (ADR-006) — real users rows, real FK, real in-memory engine
# ---------------------------------------------------------------------------


def _insert_user_row(engine: Engine, user_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            users_table.insert().values(
                id=user_id,
                created_at=datetime.now(tz=timezone.utc),
            )
        )


def _make_sqlite_engine_with_fk_enforcement() -> Engine:
    """Same in-memory schema as `_make_sqlite_engine`, but with SQLite's
    `foreign_keys` pragma turned on. SQLite ignores FK constraints by default
    (unlike NOT NULL, which it always enforces), so the FK-violation assertion
    below would silently pass without this."""
    engine = _make_sqlite_engine()

    @sqlalchemy.event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def test_save_run_is_scoped_and_isolated_by_tenant_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end ADR-006 regression guard: two real `users` rows (tenant A,
    tenant B), a run saved under tenant A via the real `save_run()` path
    (not a direct table insert), and `load_history` proving tenant B sees
    nothing while tenant A sees its own run."""
    # Override the module's `_no_db` autouse fixture: this test needs the real
    # `_write_run_row` DB-write path, not the JSON-only no-op the rest of this
    # file uses.
    monkeypatch.setattr(db, "_write_run_row", _real_write_run_row)

    engine = _make_sqlite_engine_with_fk_enforcement()
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    _insert_user_row(engine, "tenant-a")
    _insert_user_row(engine, "tenant-b")

    state = _make_completed_state()
    save_run(state, log_dir=str(tmp_path), tenant_id="tenant-a")

    history_b = db.load_history(tenant_id="tenant-b")
    assert history_b.empty

    history_a = db.load_history(tenant_id="tenant-a")
    assert list(history_a["run_id"]) == ["run-abc-123"]


def test_save_run_rejects_tenant_id_with_no_matching_user_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key regression guard for ADR-006's migration: `tenant_id` is a real
    FK to `users.id`, not a decorative string column — saving a run under a
    tenant_id with no corresponding `users` row must fail, not silently
    persist an orphaned row."""
    monkeypatch.setattr(db, "_write_run_row", _real_write_run_row)

    engine = _make_sqlite_engine_with_fk_enforcement()
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    state = _make_completed_state()
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        save_run(state, log_dir=str(tmp_path), tenant_id="ghost-tenant")


# ---------------------------------------------------------------------------
# ensure_user (fixed in 7507300) — a brand-new Clerk user has no `users` row
# yet, so their first save_run()/save_analysis() used to fail its FK.
# ---------------------------------------------------------------------------


def test_ensure_user_allows_first_save_run_for_new_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct regression guard for the bug fixed in 7507300: nothing in the
    Clerk sign-in flow used to create a `users` row for a brand-new account,
    so that account's very first `save_run()` failed its FK to `users.id`.
    `ensure_user()` must create that row so `save_run()` succeeds."""
    monkeypatch.setattr(db, "_write_run_row", _real_write_run_row)

    engine = _make_sqlite_engine_with_fk_enforcement()
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    db.ensure_user("new_user")  # no pre-existing `users` row for "new_user"

    state = _make_completed_state()
    save_run(state, log_dir=str(tmp_path), tenant_id="new_user")  # must not raise

    history = db.load_history(tenant_id="new_user")
    assert list(history["run_id"]) == ["run-abc-123"]


def test_ensure_user_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ensure_user()` upserts with `ON CONFLICT DO NOTHING` so repeat calls for
    an already-known user (e.g. every Streamlit rerun) are cheap no-ops. This
    proves that: calling it twice for the same id must not raise, must not
    create a second row, and must not clobber the original `created_at`."""
    engine = _make_sqlite_engine_with_fk_enforcement()
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    db.ensure_user("repeat_user")
    with engine.connect() as conn:
        first_created_at = conn.execute(
            sqlalchemy.select(users_table.c.created_at).where(users_table.c.id == "repeat_user")
        ).scalar()

    db.ensure_user("repeat_user")  # second call — must not raise

    with engine.connect() as conn:
        second_created_at = conn.execute(
            sqlalchemy.select(users_table.c.created_at).where(users_table.c.id == "repeat_user")
        ).scalar()
        row_count = conn.execute(
            sqlalchemy.select(sqlalchemy.func.count()).select_from(users_table)
        ).scalar()

    assert second_created_at == first_created_at
    assert row_count == 1
