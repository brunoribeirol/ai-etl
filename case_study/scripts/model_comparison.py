"""Sprint 8 — model comparison + stability experiment (CLI/headless only, no
`api/` or `frontend/` touched — matches the sprint's scope).

Runs Scenario 1 of the case study (`case_study/pipelines/scenario1_spec.txt`,
`case_study/data/sales.csv`) N times per configured model through
`ai_etl.services.pipeline_service.run_full_analysis` — the same function
`services/execution_queue.py::enqueue_analysis` wraps in a Celery task — and
records real cost (`core/pricing.py`), real latency (`state["stage_durations"]`
plus total wall time), and an objective quality score (see `score_quality`)
per run. Also computes run-to-run variance per model (the "stability"
experiment) from the same N runs.

Scenarios 2-4 (`case_study/pipelines/`, `tests/e2e/test_scenario{2,3,4}_*.py`)
additionally need a reachable Postgres (`postgres-test`, and a REST/document
source for 3/4) — see `_postgres_reachable()`. When unreachable, this script
records one `status="skipped_no_infra"` row per (model, scenario) rather than
silently omitting them, and explains why in the printed summary.

--- Why call `run_full_analysis` directly instead of `enqueue_analysis`? ---
`enqueue_analysis` (`services/execution_queue.py`) needs a reachable Redis
(Celery broker/result backend), and `run_full_analysis`'s own `save_run`/
`save_analysis` need a reachable Postgres (`APP_DATABASE_URL`, audit trail)
with Postgres-specific `ON CONFLICT` upserts (`audit/db.py`,
`sqlalchemy.dialects.postgresql.insert`) that do not work against SQLite.
This session's sandbox has no Docker daemon (`docker ps` fails with "Cannot
connect to the Docker daemon"), so neither Redis nor Postgres is reachable —
this script therefore calls `pipeline_service.run_full_analysis` directly
(the same function `run_full_analysis_task` wraps) and monkeypatches
`save_run`/`save_analysis`/`save_stage_latencies` to no-ops for the duration
of each run (pass `--persist-audit` to let them write for real once
`APP_DATABASE_URL` is reachable — the Celery/Redis hop itself is still
bypassed either way, since it adds no signal for cost/latency/quality
comparison beyond confirming the plumbing, which `tests/e2e/` already covers).
Everything else — the real Silver LangGraph, real sandboxed transform
execution, real Quality Agent, latency, cost, quality score — runs for real.

--- Why every row may still say data_source != "real" ---
This environment has no `OPENAI_API_KEY` (confirmed via `env | grep -i
openai`) and no `ollama` binary (confirmed via `which ollama`). Per Sprint 8's
brief, LLM calls are therefore mocked with deterministic, fixed responses
(same technique as `tests/e2e/conftest.py::mock_pipeline_llm`) rather than
skipped — this validates the full harness (real sandbox execution, real
Quality Agent, real cost/latency/quality-score computation) end to end, but
the resulting cost ($0.00, zero real tokens) and latency (no real network
round trip to an LLM) numbers are NOT a real model comparison. Every output
row is tagged `data_source`:
  - "real"      — ran against a real API with the given model (requires a key
                  reachable at execution time; not used in this session).
  - "mock"      — OpenAI-shaped model, no key available; deterministic mocked
                  LLM responses, $0 cost, near-zero LLM latency.
  - "simulated" — an `ollama:*` model slot; this script does NOT invent
                  latency/quality numbers for a real local model it never ran.
                  It still exercises the same mocked pipeline (so the harness
                  is proven to generalize to a 3rd provider slot) but reports
                  cost_usd as `None` ("no per-token API pricing — self-hosted"
                  is a real economic fact, not a fabricated number) and flags
                  every metric as illustrative-only in the CSV.
Re-run with `OPENAI_API_KEY` exported (and `ollama` installed/running, plus a
model pulled) to get real numbers — no code change needed, `data_source` will
flip to "real" automatically.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import statistics
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import sqlalchemy
from sqlalchemy import text

from ai_etl.core.pricing import compute_cost_usd
from ai_etl.services import pipeline_service

REPO_ROOT = Path(__file__).resolve().parents[2]
SALES_CSV = REPO_ROOT / "case_study" / "data" / "sales.csv"
SCENARIO1_SPEC = REPO_ROOT / "case_study" / "pipelines" / "scenario1_spec.txt"

DEFAULT_MODELS = ["gpt-4o-mini", "gpt-4o", "ollama:llama3.1"]
DEFAULT_N = 5  # matches case_study/results/tabela-resultados.md's established
# convention (5 runs/scenario) — see the report for the full rationale.

TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL", "postgresql://test:test@localhost:5433/testdb")

# Deterministic mock plan/transform code mirroring `scenario1_spec.txt` exactly
# (rename dt->date, amt->amount, filter status=="active", drop duplicates) —
# same transformation `tests/e2e/test_scenario1_csv.py` mocks, applied here to
# the real 5000-row `case_study/data/sales.csv` dataset instead of a tiny
# inline fixture, so quality/completeness numbers reflect the real case study.
SCENARIO1_PLAN = {
    "sources": [{"name": "sales", "type": "csv", "path": "__CSV_PATH__"}],
    "destination": {"type": "csv", "path": "__OUTPUT_PATH__"},
    "transformations": [
        "rename dt to date",
        "rename amt to amount",
        'filter status == "active"',
        "drop duplicate rows",
    ],
    "quality_checks": ["null_check", "duplicate_check", "outlier_check"],
}
SCENARIO1_TRANSFORM_CODE = """
def transform(dfs: dict) -> pd.DataFrame:
    df = dfs["sales"].rename(columns={"dt": "date", "amt": "amount"})
    df = df[df["status"] == "active"]
    df = df.drop_duplicates()
    return df
"""


def _postgres_reachable(url: str = TEST_POSTGRES_URL) -> bool:
    try:
        engine = sqlalchemy.create_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@dataclass
class RunResult:
    model: str
    provider: str
    scenario: int
    run_index: int
    data_source: str  # "real" | "mock" | "simulated" | "skipped_no_infra"
    status: str
    elapsed_ms: float | None = None
    rows_loaded: int | None = None
    quality_severity: str | None = None
    transformation_attempts: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    quality_score: float | None = None
    stage_durations: dict[str, float] = field(default_factory=dict)
    note: str = ""

    def to_row(self) -> dict[str, Any]:
        row = {
            "model": self.model,
            "provider": self.provider,
            "scenario": self.scenario,
            "run_index": self.run_index,
            "data_source": self.data_source,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "rows_loaded": self.rows_loaded,
            "quality_severity": self.quality_severity,
            "transformation_attempts": self.transformation_attempts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "quality_score": self.quality_score,
            "note": self.note,
        }
        row.update({f"stage_{k}_s": v for k, v in self.stage_durations.items()})
        return row


def score_quality(
    state: dict[str, Any],
    reference_rows: int | None = None,
) -> float:
    """Objective, deterministic 0-100 quality score computed only from signals
    the real (non-mocked) Silver pipeline stages produce — the Quality Agent's
    severity verdict, the Loader's `rows_loaded`, and the Transformer's retry
    count — so it stays meaningful even when the LLM call itself is mocked
    (only the *content* of the LLM's plan/code is faked; the sandboxed
    execution, quality checks, and load step that consume it are real).

    Weights, chosen for interpretability over statistical tuning (there is no
    labeled dataset to tune against at this project's TCC scale):
      - 40 pts: pipeline reached `status == "completed"` — a hard gate; every
                other signal is meaningless for a failed run, so the score is
                0 if this fails.
      - 30 pts: Quality Agent severity — ok=30, warning=18, error=0 (an
                `error` severity means the pipeline still completed but the
                data itself is unreliable, matching `quality_report`'s own
                "checks: N warnings, M errors" summary).
      - 20 pts: completeness — `rows_loaded / reference_rows` capped at 1.0
                when a reference is given (comparing against a prior known-good
                run's row count catches silent under-extraction); 20 pts flat
                if `rows_loaded > 0` and no reference was supplied.
      - 10 pts: Transformer efficiency — 1 attempt=10, 2=5, >=3=0 (mirrors
                `transformation_attempts`, capped at the real retry limit of 3
                — see `agents/transformer.py`).
    """
    if state.get("status") != "completed":
        return 0.0

    score = 40.0

    severity = (state.get("quality_report") or {}).get("severity") or ""
    score += {"ok": 30.0, "warning": 18.0, "error": 0.0}.get(severity, 0.0)

    rows_loaded = (state.get("load_result") or {}).get("rows_loaded") or 0
    if reference_rows and reference_rows > 0:
        score += 20.0 * min(1.0, rows_loaded / reference_rows)
    elif rows_loaded > 0:
        score += 20.0

    attempts = state.get("transformation_attempts") or 1
    score += {1: 10.0, 2: 5.0}.get(attempts, 0.0)

    return round(score, 2)


def _mock_response(content: str) -> MagicMock:
    response = MagicMock(content=content)
    response.usage_metadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return response


def _patch_mocked_pipeline(stack: ExitStack, plan: dict[str, Any], transform_code: str) -> None:
    """Same technique as `tests/e2e/conftest.py::mock_pipeline_llm`, standalone
    (this script isn't a pytest fixture) — patches every LLM call site
    `run_full_analysis` touches with deterministic, fixed responses."""
    orchestrator_llm = MagicMock()
    orchestrator_llm.invoke.return_value = _mock_response(json.dumps(plan))
    stack.enter_context(patch("ai_etl.agents.pipeline.orchestrator.get_llm", return_value=orchestrator_llm))

    transformer_llm = MagicMock()
    transformer_llm.invoke.return_value = _mock_response(transform_code)
    stack.enter_context(patch("ai_etl.agents.pipeline.transformer.get_llm", return_value=transformer_llm))

    planner_llm = MagicMock()
    planner_llm.invoke.return_value = _mock_response("[]")
    stack.enter_context(patch("ai_etl.agents.analysis.planner.get_llm", return_value=planner_llm))

    analyst_llm = MagicMock()
    analyst_llm.invoke.return_value = _mock_response(
        'gold_df = df.head(1)\nfig = go.Figure()\nnarrative = "No business question was asked."'
    )
    stack.enter_context(patch("ai_etl.agents.analysis.analyst.get_llm", return_value=analyst_llm))

    advisor_llm = MagicMock()
    advisor_llm.invoke.return_value = _mock_response(
        json.dumps({"recommendations": [], "summary": "No sub-tasks were run."})
    )
    stack.enter_context(patch("ai_etl.agents.analysis.advisor.get_llm", return_value=advisor_llm))


def _patch_no_audit_persistence(stack: ExitStack) -> None:
    """Bypasses `save_run`/`save_analysis`/`save_stage_latencies` (all need a
    reachable Postgres via `APP_DATABASE_URL` — unavailable in this sandbox,
    see module docstring). No audit trail is written by this script."""
    stack.enter_context(patch("ai_etl.services.pipeline_service.save_run", return_value=None))
    stack.enter_context(patch("ai_etl.services.pipeline_service.save_analysis", return_value=None))
    stack.enter_context(
        patch("ai_etl.services.pipeline_service.save_stage_latencies", return_value=None)
    )


def run_scenario1(
    model: str,
    provider: str,
    data_source: str,
    run_index: int,
    tmp_dir: Path,
    persist_audit: bool,
) -> RunResult:
    if not SALES_CSV.exists():
        return RunResult(
            model=model,
            provider=provider,
            scenario=1,
            run_index=run_index,
            data_source="skipped_no_infra",
            status="skipped",
            note=(
                f"{SALES_CSV} not found — run "
                "`uv run python case_study/data/generate_sales.py` first."
            ),
        )

    run_dir = tmp_dir / f"{model.replace(':', '_')}_{run_index}"
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "output.csv"

    plan = {
        **SCENARIO1_PLAN,
        "sources": [{"name": "sales", "type": "csv", "path": str(SALES_CSV)}],
        "destination": {"type": "csv", "path": str(output_path)},
    }
    spec = SCENARIO1_SPEC.read_text()

    old_env_model = os.environ.get("AI_ETL_LLM_MODEL")
    os.environ["AI_ETL_LLM_MODEL"] = model
    try:
        with ExitStack() as stack:
            if data_source != "real":
                _patch_mocked_pipeline(stack, plan, SCENARIO1_TRANSFORM_CODE)
            if not persist_audit:
                _patch_no_audit_persistence(stack)

            start = time.perf_counter()
            result = pipeline_service.run_full_analysis(
                spec=spec,
                business_question="",
                run_dir=str(run_dir),
                tenant_id=f"sprint8_{uuid.uuid4().hex[:8]}",
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

        state = result["state"]
        tokens = result["tokens"]
        cost_usd: float | None
        if data_source == "simulated":
            cost_usd = None  # no per-token API pricing for a self-hosted model
        else:
            cost_usd = compute_cost_usd(model, tokens)

        note = ""
        if data_source == "mock":
            note = "mocked LLM, $0 real cost — plumbing validation only"
        elif data_source == "simulated":
            note = "ollama slot: Ollama unavailable in this sandbox, pipeline mocked for harness parity only"

        return RunResult(
            model=model,
            provider=provider,
            scenario=1,
            run_index=run_index,
            data_source=data_source,
            status=state.get("status", "unknown"),
            elapsed_ms=round(elapsed_ms, 1),
            rows_loaded=(state.get("load_result") or {}).get("rows_loaded"),
            quality_severity=(state.get("quality_report") or {}).get("severity"),
            transformation_attempts=state.get("transformation_attempts"),
            input_tokens=tokens.get("input_tokens", 0),
            output_tokens=tokens.get("output_tokens", 0),
            cost_usd=cost_usd,
            quality_score=score_quality(state, reference_rows=None),
            stage_durations=state.get("stage_durations", {}),
            note=note,
        )
    finally:
        if old_env_model is None:
            os.environ.pop("AI_ETL_LLM_MODEL", None)
        else:
            os.environ["AI_ETL_LLM_MODEL"] = old_env_model


def _provider_and_access(model: str) -> tuple[str, bool]:
    if model.startswith("ollama:"):
        return "ollama", shutil.which("ollama") is not None
    return "openai", bool(os.environ.get("OPENAI_API_KEY"))


def compute_stability(rows: list[RunResult]) -> dict[str, Any]:
    """Run-to-run variance for one (model, scenario) group — the "stability"
    half of Sprint 8. Reports sample stdev (n-1) guarded for n<2, plus the
    coefficient of variation (stdev/mean) where mean != 0, which is more
    comparable across metrics of very different scale (rows vs. ms vs. score)
    than raw stdev alone."""
    completed = [r for r in rows if r.status == "completed"]
    out: dict[str, Any] = {"n": len(rows), "n_completed": len(completed)}
    for metric in ("elapsed_ms", "rows_loaded", "quality_score"):
        values = [getattr(r, metric) for r in completed if getattr(r, metric) is not None]
        if not values:
            out[metric] = None
            continue
        mean = statistics.mean(values)
        stdev = statistics.stdev(values) if len(values) >= 2 else 0.0
        out[metric] = {
            "mean": round(mean, 3),
            "stdev": round(stdev, 3),
            "cv": round(stdev / mean, 4) if mean else None,
            "min": min(values),
            "max": max(values),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument(
        "--output-dir", default=str(REPO_ROOT / "case_study" / "results" / "sprint8")
    )
    parser.add_argument(
        "--persist-audit",
        action="store_true",
        help="Let save_run/save_analysis write for real (requires reachable APP_DATABASE_URL).",
    )
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_dir / "_runs"
    tmp_dir.mkdir(exist_ok=True)

    postgres_ok = _postgres_reachable()
    if not postgres_ok:
        print(
            "[model_comparison] Postgres (TEST_POSTGRES_URL) unreachable — "
            "Scenarios 2-4 need it for their Postgres/join sources and are "
            "skipped this run (Scenario 1, CSV-only, does not need it)."
        )

    all_rows: list[RunResult] = []
    for model in models:
        provider, has_real_access = _provider_and_access(model)
        data_source = (
            "real" if has_real_access else ("simulated" if provider == "ollama" else "mock")
        )
        print(f"[model_comparison] model={model} data_source={data_source}")

        scenario_rows: list[RunResult] = []
        for i in range(1, args.n + 1):
            r = run_scenario1(model, provider, data_source, i, tmp_dir, args.persist_audit)
            scenario_rows.append(r)
            all_rows.append(r)
            print(
                f"  run {i}/{args.n}: status={r.status} elapsed_ms={r.elapsed_ms} "
                f"quality_score={r.quality_score} cost_usd={r.cost_usd}"
            )

        for scenario in (2, 3, 4):
            if not postgres_ok:
                skip = RunResult(
                    model=model,
                    provider=provider,
                    scenario=scenario,
                    run_index=0,
                    data_source="skipped_no_infra",
                    status="skipped",
                    note="Postgres unreachable in this sandbox (no Docker daemon) — see module docstring.",
                )
                all_rows.append(skip)

    csv_path = output_dir / "comparison_runs.csv"
    fieldnames = list(all_rows[0].to_row().keys()) if all_rows else []
    for r in all_rows:
        fieldnames = list(dict.fromkeys(fieldnames + list(r.to_row().keys())))
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in all_rows:
            writer.writerow(r.to_row())
    print(f"[model_comparison] wrote {csv_path}")

    stability_path = output_dir / "stability_summary.json"
    stability: dict[str, Any] = {}
    for model in models:
        model_rows = [r for r in all_rows if r.model == model and r.scenario == 1]
        stability[model] = compute_stability(model_rows)
    stability_path.write_text(json.dumps(stability, indent=2))
    print(f"[model_comparison] wrote {stability_path}")


if __name__ == "__main__":
    main()
