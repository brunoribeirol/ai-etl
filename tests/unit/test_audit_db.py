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
from ai_etl.audit.db import load_full_result, save_analysis, save_run, save_stage_latencies
from ai_etl.audit.models import analysis_runs as analysis_runs_table
from ai_etl.audit.models import metadata as audit_metadata
from ai_etl.audit.models import runs as runs_table
from ai_etl.audit.models import stage_latencies as stage_latencies_table
from ai_etl.audit.models import users as users_table
from ai_etl.core.state import initial_state

_ZERO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        db, "_write_run_row", lambda state, tenant_id=None, saved_pipeline_id=None: None
    )
    monkeypatch.setattr(db, "_write_analysis_row", lambda *args, **kwargs: None)


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
    json_key = save_run(state, log_dir=str(tmp_path))
    json_path = tmp_path / json_key

    assert json_path.exists()
    assert json_path.suffix == ".json"
    data = json.loads(json_path.read_text())
    assert data["run_id"] == "run-abc-123"
    assert data["status"] == "completed"


def test_save_run_writes_row_with_correct_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        db,
        "_write_run_row",
        lambda state, tenant_id=None, saved_pipeline_id=None: captured.update(state),
    )

    state = _make_completed_state()
    save_run(state, log_dir=str(tmp_path))

    assert captured["run_id"] == "run-abc-123"
    assert captured["status"] == "completed"
    assert captured["load_result"]["rows_loaded"] == 42


def test_save_run_failed_state_has_no_load_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        db,
        "_write_run_row",
        lambda state, tenant_id=None, saved_pipeline_id=None: captured.update(state),
    )

    state = _make_failed_state()
    save_run(state, log_dir=str(tmp_path))

    assert captured.get("load_result") is None


def test_save_run_json_serializes_dataframe(tmp_path: Path) -> None:
    state = _make_completed_state()
    state["transformed_data"] = pd.DataFrame({"a": [1, 2]})
    json_key = save_run(state, log_dir=str(tmp_path))

    data = json.loads((tmp_path / json_key).read_text())
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
    json_key = save_run(state, log_dir=str(tmp_path))

    data = json.loads((tmp_path / json_key).read_text())
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
    json_key = save_analysis(
        "run-analysis-1",
        [_make_gold_result()],
        [_make_science_result()],
        _make_advisor_result(),
        _ZERO_TOKENS,
        log_dir=str(tmp_path),
    )

    json_path = tmp_path / json_key
    assert json_path.exists()
    data = json.loads(json_path.read_text())
    assert data["run_id"] == "run-analysis-1"
    assert len(data["gold"]) == 1
    assert data["gold"][0]["task_question"] == "Quais os KPIs?"
    assert data["gold"][0]["narrative"] == "Produto B lidera."
    assert len(data["science"]) == 1
    assert data["advisor"]["summary"] == "Foco no produto B."


def test_save_analysis_includes_data_preview(tmp_path: Path) -> None:
    json_key = save_analysis(
        "run-analysis-2",
        [_make_gold_result()],
        [],
        _make_advisor_result(),
        _ZERO_TOKENS,
        log_dir=str(tmp_path),
    )

    data = json.loads((tmp_path / json_key).read_text())
    assert data["gold"][0]["data_shape"] == [2, 2]
    assert len(data["gold"][0]["data_preview"]) == 2


def test_save_analysis_aggregates_tokens_before_writing_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_write(
        run_id: str,
        n_gold: int,
        n_science: int,
        tokens: dict,
        tenant_id: str | None = None,
        saved_pipeline_id: str | None = None,
    ) -> None:
        captured.update(
            run_id=run_id,
            n_gold=n_gold,
            n_science=n_science,
            tokens=tokens,
            tenant_id=tenant_id,
            saved_pipeline_id=saved_pipeline_id,
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
    json_key = save_analysis(
        "run-analysis-4", [], [], _make_advisor_result(), _ZERO_TOKENS, log_dir=str(tmp_path)
    )

    data = json.loads((tmp_path / json_key).read_text())
    assert data["gold"] == []
    assert data["science"] == []


def test_save_analysis_marks_repaired_subtasks(tmp_path: Path) -> None:
    repaired_gold = {**_make_gold_result(), "repaired": True}
    json_key = save_analysis(
        "run-analysis-5",
        [repaired_gold],
        [],
        _make_advisor_result(),
        _ZERO_TOKENS,
        log_dir=str(tmp_path),
    )

    data = json.loads((tmp_path / json_key).read_text())
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


def test_load_history_includes_null_cost_for_silver_only_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint 3 (ADR-008): a run with no matching `analysis_runs` row (Silver
    ran, no business question was asked) must read back `cost_usd`/
    `model_name` as null via the `LEFT OUTER JOIN`, not raise or silently
    drop the row."""
    engine = _make_sqlite_engine()
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    _insert_run_row(engine, "run-silver-only", tenant_id="tenant-a")

    history = db.load_history(tenant_id="tenant-a")

    assert list(history["run_id"]) == ["run-silver-only"]
    assert history["cost_usd"].isna().all()
    assert history["model_name"].isna().all()


def test_load_history_surfaces_cost_for_analyzed_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _make_sqlite_engine()
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    _insert_run_row(engine, "run-with-analysis", tenant_id="tenant-a")
    with engine.begin() as conn:
        conn.execute(
            analysis_runs_table.insert().values(
                run_id="run-with-analysis",
                gold_subtasks=1,
                science_subtasks=0,
                input_tokens=1000,
                output_tokens=500,
                total_tokens=1500,
                timestamp=datetime.now(tz=timezone.utc),
                tenant_id="tenant-a",
                model_name="gpt-4o-mini",
                cost_usd=0.00045,
            )
        )

    history = db.load_history(tenant_id="tenant-a")

    assert history.loc[history["run_id"] == "run-with-analysis", "model_name"].iloc[0] == (
        "gpt-4o-mini"
    )
    assert history.loc[history["run_id"] == "run-with-analysis", "cost_usd"].iloc[
        0
    ] == pytest.approx(0.00045)


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
    below would silently pass without this.

    The `connect` listener must be registered *before* `create_all()` runs,
    not after: `sqlite:///:memory:` engines use `SingletonThreadPool`, which
    keeps the connection opened by `create_all()` alive and reuses it for
    every later `.begin()`/`.connect()` call on this thread. Registering the
    listener on an already-built `_make_sqlite_engine()` engine attaches it
    too late — that first connection was already made without the pragma,
    and no new connection is ever opened to trigger it, so FK enforcement
    was silently never applied (the bug this comment now prevents).
    """
    engine = sqlalchemy.create_engine("sqlite:///:memory:")

    @sqlalchemy.event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    audit_metadata.create_all(engine)
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


# ---------------------------------------------------------------------------
# save_stage_latencies (ADR-007) — same in-memory-SQLite-with-FK-enforcement
# pattern as the tenant-isolation tests above, since `stage_latencies.tenant_id`
# is a real FK to `users.id` just like `runs`/`analysis_runs`.
# ---------------------------------------------------------------------------


def _select_stage_latencies(engine: Engine, run_id: str | None = None) -> list[dict]:
    stmt = sqlalchemy.select(stage_latencies_table)
    if run_id is not None:
        stmt = stmt.where(stage_latencies_table.c.run_id == run_id)
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings().all()]


def test_save_stage_latencies_silver_dict_form_inserts_one_row_per_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silver's `state["stage_durations"]` is a flat {stage: seconds} dict — one
    LangGraph node run exactly once each, so every row's `seq` is 1."""
    engine = _make_sqlite_engine_with_fk_enforcement()
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    _insert_user_row(engine, "tenant-a")

    durations = {"orchestrator": 0.5, "extractor": 1.2, "transformer": 3.0}
    save_stage_latencies("run-1", "silver", "tenant-a", durations)

    rows = _select_stage_latencies(engine, "run-1")
    assert len(rows) == 3
    by_stage = {r["stage"]: r for r in rows}
    assert by_stage["orchestrator"]["duration_seconds"] == 0.5
    assert all(r["run_type"] == "silver" for r in rows)
    assert all(r["tenant_id"] == "tenant-a" for r in rows)
    assert all(r["seq"] == 1 for r in rows)
    assert all(r["timed_out"] is False for r in rows)


def test_save_stage_latencies_list_form_increments_seq_per_repeat_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Analyst/Science's call-ordered list form: a repeated stage (e.g. a repair
    rerun) gets an incrementing `seq` rather than overwriting the first row."""
    engine = _make_sqlite_engine_with_fk_enforcement()
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    _insert_user_row(engine, "tenant-a")

    entries = [
        {"stage": "analyst", "duration_seconds": 1.0, "timed_out": False},
        {"stage": "science", "duration_seconds": 2.0, "timed_out": False},
        {"stage": "analyst", "duration_seconds": 0.8, "timed_out": True},  # repair rerun
    ]
    save_stage_latencies("run-2", "analysis", "tenant-a", entries)

    rows = _select_stage_latencies(engine, "run-2")
    assert len(rows) == 3
    analyst_rows = sorted((r for r in rows if r["stage"] == "analyst"), key=lambda r: r["seq"])
    assert [r["seq"] for r in analyst_rows] == [1, 2]
    assert analyst_rows[0]["timed_out"] is False
    assert analyst_rows[1]["timed_out"] is True
    science_rows = [r for r in rows if r["stage"] == "science"]
    assert science_rows[0]["seq"] == 1
    assert all(r["run_type"] == "analysis" for r in rows)


def test_save_stage_latencies_empty_durations_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _make_sqlite_engine_with_fk_enforcement()
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    _insert_user_row(engine, "tenant-a")

    save_stage_latencies("run-3", "silver", "tenant-a", {})
    save_stage_latencies("run-3", "analysis", "tenant-a", [])

    assert _select_stage_latencies(engine, "run-3") == []


def test_save_stage_latencies_rejects_tenant_id_with_no_matching_user_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same FK-integrity guarantee as `runs`/`analysis_runs` — `tenant_id` must
    reference a real `users` row, not just be a decorative string column."""
    engine = _make_sqlite_engine_with_fk_enforcement()
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    with pytest.raises(sqlalchemy.exc.IntegrityError):
        save_stage_latencies("run-4", "silver", "ghost-tenant", {"extractor": 1.0})


def test_save_stage_latencies_scoped_by_run_id_and_run_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two batches for the same tenant and same `run_id` but different
    `run_type` (silver vs. analysis — legitimate per ADR-007's schema, since
    `run_id` alone doesn't disambiguate the parent table) don't bleed into
    each other's rows."""
    engine = _make_sqlite_engine_with_fk_enforcement()
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    _insert_user_row(engine, "tenant-a")

    save_stage_latencies("run-5", "silver", "tenant-a", {"extractor": 1.0})
    save_stage_latencies(
        "run-5", "analysis", "tenant-a", [{"stage": "analyst", "duration_seconds": 2.0}]
    )

    rows = _select_stage_latencies(engine, "run-5")
    silver_rows = [r for r in rows if r["run_type"] == "silver"]
    analysis_rows = [r for r in rows if r["run_type"] == "analysis"]
    assert len(silver_rows) == 1
    assert silver_rows[0]["stage"] == "extractor"
    assert len(analysis_rows) == 1


# ---------------------------------------------------------------------------
# load_full_result (Sprint 3, ADR-008) — reconstructing _render_results' input
# from what save_run/save_analysis persist to disk, for the History tab and a
# completed async run alike.
# ---------------------------------------------------------------------------
def _insert_analysis_runs_row(engine: Engine, run_id: str, tokens: dict) -> None:
    with engine.begin() as conn:
        conn.execute(
            analysis_runs_table.insert().values(
                run_id=run_id,
                gold_subtasks=1,
                science_subtasks=0,
                input_tokens=tokens["input_tokens"],
                output_tokens=tokens["output_tokens"],
                total_tokens=tokens["total_tokens"],
                timestamp=datetime.now(tz=timezone.utc),
                tenant_id="tenant-x",
                model_name="gpt-4o-mini",
                cost_usd=0.001,
            )
        )


def test_load_full_result_reconstructs_dataframes_and_figure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import plotly.graph_objects as go

    engine = _make_sqlite_engine()
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    state = _make_completed_state()
    state["transformed_data"] = pd.DataFrame({"product": ["A", "B"], "total": [10, 20]})
    save_run(state, log_dir=str(tmp_path))

    gold = {**_make_gold_result(), "fig": go.Figure(data=[go.Bar(x=["A"], y=[1])])}
    save_analysis(
        state["run_id"],
        [gold],
        [_make_science_result()],
        _make_advisor_result(),
        _ZERO_TOKENS,
        log_dir=str(tmp_path),
        business_question="Quais os KPIs?",
    )
    _insert_analysis_runs_row(
        engine, state["run_id"], {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    )

    result = load_full_result(state["run_id"], log_dir=str(tmp_path))

    assert result is not None
    assert result["bronze"] is None
    assert isinstance(result["state"]["transformed_data"], pd.DataFrame)
    assert result["state"]["transformed_data"].shape == (2, 2)
    assert result["question"] == "Quais os KPIs?"
    assert isinstance(result["gold"][0]["gold_df"], pd.DataFrame)
    assert isinstance(result["gold"][0]["fig"], go.Figure)
    assert isinstance(result["science"][0]["predictions_df"], pd.DataFrame)
    assert result["tokens"] == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def test_load_full_result_returns_none_for_unknown_run(tmp_path: Path) -> None:
    assert load_full_result("no-such-run", log_dir=str(tmp_path)) is None


def test_load_full_result_rejects_wrong_tenant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security regression test: `load_full_result` must not return another
    tenant's artifacts even when the caller supplies a valid `run_id` — the
    only thing that made this reachable before was `app.py` always sourcing
    `run_id` from that tenant's own `load_history(...)`, a UI-layer
    restriction, not a data-layer one. See this project's Sprint A leak for
    why that distinction matters."""
    engine = _make_sqlite_engine()
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    monkeypatch.setattr(db, "_write_run_row", _real_write_run_row)

    state = _make_completed_state()
    save_run(state, log_dir=str(tmp_path), tenant_id="tenant-a")

    assert (
        load_full_result(state["run_id"], log_dir=str(tmp_path), tenant_id="tenant-a") is not None
    )
    assert load_full_result(state["run_id"], log_dir=str(tmp_path), tenant_id="tenant-b") is None
    # Callers that don't pass tenant_id at all keep the old, unscoped behavior
    # (e.g. any future internal/admin tooling) — only an explicit mismatch
    # is rejected, not the absence of a tenant_id.
    assert load_full_result(state["run_id"], log_dir=str(tmp_path)) is not None


def test_load_full_result_degrades_gracefully_when_csv_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gold entry whose CSV artifact was deleted/corrupted still yields its
    narrative/task_question — just without gold_df — rather than failing the
    whole reload."""
    engine = _make_sqlite_engine()
    monkeypatch.setattr(db, "get_engine", lambda: engine)

    state = _make_completed_state()
    save_run(state, log_dir=str(tmp_path))
    save_analysis(
        state["run_id"],
        [_make_gold_result()],
        [],
        _make_advisor_result(),
        _ZERO_TOKENS,
        log_dir=str(tmp_path),
    )
    (tmp_path / f"{state['run_id']}_gold_0.csv").unlink()

    result = load_full_result(state["run_id"], log_dir=str(tmp_path))

    assert result is not None
    assert result["gold"][0]["narrative"] == "Produto B lidera."
    assert "gold_df" not in result["gold"][0]
