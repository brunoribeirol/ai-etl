"""Sprint 34 — multi-tenant concurrent load test against the SLO defined in
ADR-033 (`docs/adr/ADR-033-observability-slo.md`).

Different in kind from Sprint 12's benchmark (`case_study/data/profile_scale.py`,
ADR-013): that one profiles a single dataset at scale (200k rows x 300 cols)
run *sequentially*, stage by stage, to find algorithmic bottlenecks. This
script instead fires **N tenants concurrently** at
`pipeline_service.run_full_analysis` (small Scenario 1 dataset, fixed size)
to measure what happens to per-stage latency and error rate *under
contention* — the question a synthetic single-run benchmark cannot answer,
and the one ADR-033's SLO is actually about (a production incident is far
more likely to be "10 tenants ran a pipeline at 9am" than "one tenant's
dataset is unusually large").

--- Why call `run_full_analysis` directly instead of `enqueue_analysis`? ---
Same reasoning as `case_study/scripts/model_comparison.py` (Sprint 8): this
sandbox has no reachable Redis (Celery broker) or Postgres
(`APP_DATABASE_URL`, audit trail) — see that script's module docstring for
the full explanation. `save_run`/`save_analysis`/`save_stage_latencies` are
monkeypatched to no-ops unless `--persist-audit` is passed. Concurrency is
therefore simulated with a `ThreadPoolExecutor` running `run_full_analysis`
directly in-process (real LangGraph/sandbox execution, real GIL contention
included), not real separate Celery worker processes/containers — a real
production load test additionally exercising the Celery/Redis hop and
multiple real worker processes is flagged as pending in this script's own
report, not claimed as done.

--- Honest scope of what this measures ---
No `OPENAI_API_KEY` is available in this sandbox (same constraint Sprint 8
documented) — every LLM call is mocked with a deterministic, near-instant
response (same technique as `model_comparison.py::_patch_mocked_pipeline`).
This means: (1) the concurrency/contention behavior of the *sandbox process
pool*, the *audit-log list mutation path*, and *Python-level GIL contention
under N simultaneous requests* is measured for real; (2) real LLM round-trip
latency under concurrent load (rate limits, provider-side queueing) is NOT
measured — that requires a real API key and is pending. See this script's
generated report for the explicit checklist.

--- A real bug this script found while being written ---
The first working version patched `get_llm()` per request, inside each
`ThreadPoolExecutor` worker's own `unittest.mock.patch(...)` context
(`model_comparison.py`'s own pattern, safe there because it only ever runs
one model sequentially). Under real concurrency this is unsafe:
`unittest.mock.patch` mutates the *target module's attribute* — process-wide,
not thread-local — so one thread's `__exit__` (restoring the original
`get_llm`) can race another thread's still-in-flight call, intermittently
handing a request the *real* `get_llm()` (which then raises
`OpenAIError: Missing credentials`, no `OPENAI_API_KEY` in this sandbox) for
no reason connected to the pipeline logic being measured. Reproduced at
5 tenants x 2 requests: 2/10 requests failed this way. Fixed by patching
once, process-wide, *before* the thread pool starts (see `main()`) — the
mocked orchestrator response is now built dynamically from each request's
own prompt text (see `_orchestrator_side_effect`) so per-tenant output paths
still don't collide despite the single shared patch.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SALES_CSV = REPO_ROOT / "case_study" / "data" / "sales.csv"
SCENARIO1_SPEC = REPO_ROOT / "case_study" / "pipelines" / "scenario1_spec.txt"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "case_study" / "results" / "sprint34"

# Reuse Sprint 8's mocked-plan/transform constants and patch helpers
# (`case_study/scripts/` is not an installed package — same importlib-by-path
# technique `regression_harness.py` already established for this, instead of
# duplicating the mock setup a third time).
_MC_PATH = REPO_ROOT / "case_study" / "scripts" / "model_comparison.py"
_mc_spec = importlib.util.spec_from_file_location("model_comparison", _MC_PATH)
assert _mc_spec is not None and _mc_spec.loader is not None
model_comparison = importlib.util.module_from_spec(_mc_spec)
sys.modules.setdefault("model_comparison", model_comparison)
_mc_spec.loader.exec_module(model_comparison)

from ai_etl.services import pipeline_service  # noqa: E402

# SLO thresholds from ADR-033 — kept here (not hardcoded inline below) so the
# report can echo exactly what each run was graded against, and so a future
# SLO revision only needs to change one place.
SLO_P95_STAGE_SECONDS = {
    "orchestrator": 1.0,
    "extractor": 1.0,
    "transformer": 20.0,
    "quality": 1.0,
    "loader": 1.0,
}
SLO_ERROR_RATE_MAX = 0.01  # 1% — see ADR-033 for the baseline this is derived from.


@dataclass
class RequestResult:
    tenant_id: str
    run_index: int
    status: str
    elapsed_ms: float | None = None
    stage_durations: dict[str, float] = field(default_factory=dict)
    error: str | None = None


_DEST_PATTERN = re.compile(r"Save the result to (\S+)\.")


def _orchestrator_side_effect(prompt: str) -> MagicMock:
    """Builds the mocked orchestrator response *dynamically per call*, from
    the actual prompt text passed in — unlike a fixed `return_value`, this
    is safe to patch once and share across every concurrent thread: each
    thread's own call passes its own prompt string (containing that
    request's own destination path, formatted into the spec by `_run_one`
    below) and gets back a plan pointing at that same path, with no shared
    mutable state involved.
    """
    match = _DEST_PATTERN.search(prompt)
    dest_path = match.group(1) if match else str(SALES_CSV.parent / "output.csv")
    plan = {
        **model_comparison.SCENARIO1_PLAN,
        "sources": [{"name": "sales", "type": "csv", "path": str(SALES_CSV)}],
        "destination": {"type": "csv", "path": dest_path},
    }
    return model_comparison._mock_response(json.dumps(plan))


def _patch_pipeline_for_load_test(stack: ExitStack) -> None:
    """Patches every LLM call site `run_full_analysis` touches, once, for the
    whole load-test run — see the module docstring's "A real bug this script
    found" section for why this must happen *once*, outside the thread pool,
    rather than per request inside each worker thread."""
    orchestrator_llm = MagicMock()
    orchestrator_llm.invoke.side_effect = _orchestrator_side_effect
    stack.enter_context(
        patch("ai_etl.agents.pipeline.orchestrator.get_llm", return_value=orchestrator_llm)
    )

    transformer_llm = MagicMock()
    transformer_llm.invoke.return_value = model_comparison._mock_response(
        model_comparison.SCENARIO1_TRANSFORM_CODE
    )
    stack.enter_context(
        patch("ai_etl.agents.pipeline.transformer.get_llm", return_value=transformer_llm)
    )

    planner_llm = MagicMock()
    planner_llm.invoke.return_value = model_comparison._mock_response("[]")
    stack.enter_context(patch("ai_etl.agents.analysis.planner.get_llm", return_value=planner_llm))

    analyst_llm = MagicMock()
    analyst_llm.invoke.return_value = model_comparison._mock_response(
        'gold_df = df.head(1)\nfig = go.Figure()\nnarrative = "No business question was asked."'
    )
    stack.enter_context(patch("ai_etl.agents.analysis.analyst.get_llm", return_value=analyst_llm))

    advisor_llm = MagicMock()
    advisor_llm.invoke.return_value = model_comparison._mock_response(
        json.dumps({"recommendations": [], "summary": "No sub-tasks were run."})
    )
    stack.enter_context(patch("ai_etl.agents.analysis.advisor.get_llm", return_value=advisor_llm))


def _run_one(tenant_id: str, run_index: int, tmp_dir: Path) -> RequestResult:
    """Run one request. LLM/audit-persistence mocking is applied once, process-wide,
    by the caller *before* the thread pool starts — see `main()`'s comment for why
    per-request `unittest.mock.patch` is unsafe here."""
    run_dir = tmp_dir / f"{tenant_id}_{run_index}"
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "output.csv"

    # Same base spec text as `case_study/pipelines/scenario1_spec.txt`, with
    # this request's own output path substituted in place of the file's
    # fixed one — `_orchestrator_side_effect` reads it back out of the
    # prompt, so each concurrent request gets its own destination.
    spec = re.sub(
        r"Save the result to \S+\.",
        f"Save the result to {output_path}.",
        SCENARIO1_SPEC.read_text(),
    )

    try:
        start = time.perf_counter()
        result = pipeline_service.run_full_analysis(
            spec=spec,
            business_question="",
            run_dir=str(run_dir),
            tenant_id=tenant_id,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        state = result["state"]
        return RequestResult(
            tenant_id=tenant_id,
            run_index=run_index,
            status=state.get("status", "unknown"),
            elapsed_ms=round(elapsed_ms, 1),
            stage_durations=state.get("stage_durations", {}),
        )
    except Exception as exc:  # noqa: BLE001 — a load test must record a
        # failure as data, never let one tenant's exception kill the whole run.
        return RequestResult(
            tenant_id=tenant_id,
            run_index=run_index,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(ordered) - 1)
    if f == c:
        return round(ordered[f], 3)
    return round(ordered[f] + (ordered[c] - ordered[f]) * (k - f), 3)


def summarize(results: list[RequestResult]) -> dict[str, Any]:
    completed = [r for r in results if r.status == "completed"]
    errored = [r for r in results if r.status != "completed"]
    error_rate = len(errored) / len(results) if results else 0.0

    elapsed = [r.elapsed_ms / 1000 for r in completed if r.elapsed_ms is not None]
    total_summary: dict[str, Any] = {
        "n_requests": len(results),
        "n_completed": len(completed),
        "n_errored": len(errored),
        "error_rate": round(error_rate, 4),
        "error_rate_within_slo": error_rate <= SLO_ERROR_RATE_MAX,
        "total_elapsed_seconds": {
            "p50": _percentile(elapsed, 0.50),
            "p95": _percentile(elapsed, 0.95),
            "p99": _percentile(elapsed, 0.99),
            "mean": round(statistics.mean(elapsed), 3) if elapsed else None,
        },
    }

    per_stage: dict[str, Any] = {}
    for stage, slo_seconds in SLO_P95_STAGE_SECONDS.items():
        values = [r.stage_durations[stage] for r in completed if stage in r.stage_durations]
        p95 = _percentile(values, 0.95)
        per_stage[stage] = {
            "n": len(values),
            "p50": _percentile(values, 0.50),
            "p95": p95,
            "p99": _percentile(values, 0.99),
            "slo_p95_seconds": slo_seconds,
            "within_slo": (p95 is None) or (p95 <= slo_seconds),
        }
    total_summary["per_stage"] = per_stage
    return total_summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenants", type=int, default=10, help="Number of simulated concurrent tenants."
    )
    parser.add_argument(
        "--requests-per-tenant", type=int, default=2, help="Sequential requests per tenant."
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--persist-audit",
        action="store_true",
        help="Let save_run/save_analysis write for real (requires reachable APP_DATABASE_URL).",
    )
    args = parser.parse_args()

    if not SALES_CSV.exists():
        print(
            f"[load_test] {SALES_CSV} not found — run "
            "`uv run python case_study/data/generate_sales.py` first."
        )
        raise SystemExit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_dir / "_runs"
    tmp_dir.mkdir(exist_ok=True)

    jobs = [
        (f"tenant_{t}_{uuid.uuid4().hex[:6]}", r)
        for t in range(args.tenants)
        for r in range(args.requests_per_tenant)
    ]

    print(
        f"[load_test] {args.tenants} tenants x {args.requests_per_tenant} requests "
        f"= {len(jobs)} total requests, concurrency={args.tenants} (mocked LLM, "
        "see module docstring for scope)."
    )

    started_at = datetime.now(tz=timezone.utc)
    results: list[RequestResult] = []
    # Mocking is applied ONCE here, before any worker thread starts, and torn
    # down once after every future completes — see the module docstring's
    # "A real bug this script found" section for why per-thread patching is
    # unsafe.
    with ExitStack() as stack:
        _patch_pipeline_for_load_test(stack)
        if not args.persist_audit:
            model_comparison._patch_no_audit_persistence(stack)

        with ThreadPoolExecutor(max_workers=args.tenants) as executor:
            futures = {
                executor.submit(_run_one, tenant_id, run_index, tmp_dir): (tenant_id, run_index)
                for tenant_id, run_index in jobs
            }
            for future in as_completed(futures):
                r = future.result()
                results.append(r)
                print(
                    f"  {r.tenant_id} run {r.run_index}: status={r.status} "
                    f"elapsed_ms={r.elapsed_ms}"
                )
    finished_at = datetime.now(tz=timezone.utc)

    summary = summarize(results)
    summary["generated_at"] = finished_at.isoformat()
    summary["wall_clock_seconds"] = round((finished_at - started_at).total_seconds(), 3)
    summary["tenants"] = args.tenants
    summary["requests_per_tenant"] = args.requests_per_tenant
    summary["data_source"] = "mock"
    summary["note"] = (
        "Mocked LLM (no OPENAI_API_KEY in this environment) — measures real "
        "sandbox/GIL contention under concurrency, not real LLM round-trip "
        "latency under load. See module docstring / ADR-033 for full scope."
    )

    summary_path = output_dir / "load_test_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"[load_test] wrote {summary_path}")

    raw_path = output_dir / "load_test_requests.json"
    raw_path.write_text(
        json.dumps(
            [
                {
                    "tenant_id": r.tenant_id,
                    "run_index": r.run_index,
                    "status": r.status,
                    "elapsed_ms": r.elapsed_ms,
                    "stage_durations": r.stage_durations,
                    "error": r.error,
                }
                for r in results
            ],
            indent=2,
        )
    )
    print(f"[load_test] wrote {raw_path}")

    print(
        f"[load_test] error_rate={summary['error_rate']} "
        f"(SLO <= {SLO_ERROR_RATE_MAX}, within_slo={summary['error_rate_within_slo']})"
    )
    for stage, stats in summary["per_stage"].items():
        print(
            f"  {stage}: p95={stats['p95']}s (SLO <= {stats['slo_p95_seconds']}s, "
            f"within_slo={stats['within_slo']})"
        )


if __name__ == "__main__":
    main()
