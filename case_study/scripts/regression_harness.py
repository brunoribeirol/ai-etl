"""Sprint 28 — prompt/agent regression harness (ADR-029).

Runs a larger, more adversarial corpus of scenarios (`case_study/scenarios/*.json`) through
`ai_etl.services.pipeline_service.run_full_analysis` — the same entry point Sprint 8's
`model_comparison.py` uses, and the same one `services/execution_queue.py::enqueue_analysis`
wraps for a real production run — and compares the resulting quality metrics against a
committed baseline (`case_study/results/sprint28/baseline_metrics.json`) before a
prompt/agent/model change is promoted.

--- Trigger: manual only (see ADR-029 Decision 1) ---
This script is invoked by hand (or via `.github/workflows/prompt-regression.yml`'s
`workflow_dispatch`), never automatically on every push/PR — real LLM calls cost real money,
and this project's own sandboxed dev sessions frequently have no `OPENAI_API_KEY` at all
(same documented gap as Sprints 8/12/22).

--- Quality signal: reused, not reinvented (ADR-029 Decision 2) ---
Per-scenario quality is Sprint 8's own `score_quality(state)` (0-100, Silver-layer) plus a
sanity-warning count read directly off `run_full_analysis`'s own `gold`/`science` results —
Sprint 21 (ADR-026)'s `check_gold_output`/`check_science_output` are already computed and
attached there by `pipeline_service.run_gold_analysis`/`run_science_analysis`; this harness
does not call them separately.

--- data_source honesty (same convention as Sprint 8) ---
Every scenario result is tagged `data_source`: "real" (a real OPENAI_API_KEY was available —
the harness ran the actual Orchestrator/Transformer/Planner/Analyst/Science prompts against a
real model) or "mock" (no key available — every LLM call site is patched with a deterministic,
scenario-defined fixed response; this still exercises the full deterministic pipeline — real
extraction, real sandboxed execution, real Quality Agent, real output_validation checks — but
CANNOT catch a prompt-wording regression, only a regression in the surrounding code). Re-run
with `OPENAI_API_KEY` exported to get a real signal — no code change needed.

Usage:
    uv run python case_study/scripts/regression_harness.py
    uv run python case_study/scripts/regression_harness.py --update-baseline
    uv run python case_study/scripts/regression_harness.py --scenarios sales_revenue_by_region
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "case_study" / "scenarios"
RESULTS_DIR = REPO_ROOT / "case_study" / "results" / "sprint28"
BASELINE_PATH = RESULTS_DIR / "baseline_metrics.json"
LATEST_RUN_PATH = RESULTS_DIR / "latest_run.json"

# Default tolerance a corpus-wide or per-scenario score_quality drop must exceed before it's
# flagged as a regression — absorbs normal LLM run-to-run variance (Sprint 8's own "stability"
# experiment found real variance across identical repeated runs); not a rubber stamp for any
# drop, just for noise.
DEFAULT_QUALITY_TOLERANCE = 5.0

# Reuse Sprint 8's harness (score_quality + the module-loading technique
# tests/unit/test_model_comparison.py already uses, since case_study/scripts/ is not an
# installed package) instead of duplicating it.
_MC_PATH = REPO_ROOT / "case_study" / "scripts" / "model_comparison.py"
_mc_spec = importlib.util.spec_from_file_location("model_comparison", _MC_PATH)
assert _mc_spec is not None and _mc_spec.loader is not None
model_comparison = importlib.util.module_from_spec(_mc_spec)
sys.modules.setdefault("model_comparison", model_comparison)
_mc_spec.loader.exec_module(model_comparison)
score_quality = model_comparison.score_quality

from ai_etl.services import pipeline_service  # noqa: E402


def _mock_response(content: str) -> MagicMock:
    response = MagicMock(content=content)
    response.usage_metadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return response


def _patch_no_audit_persistence(stack: ExitStack) -> None:
    """Bypasses save_run/save_analysis/save_stage_latencies — same reason as Sprint 8's
    own `_patch_no_audit_persistence`: this harness needs no reachable Postgres."""
    stack.enter_context(patch("ai_etl.services.pipeline_service.save_run", return_value=None))
    stack.enter_context(patch("ai_etl.services.pipeline_service.save_analysis", return_value=None))
    stack.enter_context(
        patch("ai_etl.services.pipeline_service.save_stage_latencies", return_value=None)
    )


def _patch_mocked_pipeline(stack: ExitStack, plan: dict[str, Any], mock: dict[str, Any]) -> None:
    """Patches every LLM call site `run_full_analysis` touches with the scenario's own
    deterministic fixed responses (`mock` block of its JSON definition)."""
    orchestrator_llm = MagicMock()
    orchestrator_llm.invoke.return_value = _mock_response(json.dumps(plan))
    stack.enter_context(patch("ai_etl.agents.pipeline.orchestrator.get_llm", return_value=orchestrator_llm))

    transformer_llm = MagicMock()
    transformer_llm.invoke.return_value = _mock_response(mock["transform_code"])
    stack.enter_context(patch("ai_etl.agents.pipeline.transformer.get_llm", return_value=transformer_llm))

    planner_llm = MagicMock()
    planner_llm.invoke.return_value = _mock_response(json.dumps(mock["planner_response"]))
    stack.enter_context(patch("ai_etl.agents.analysis.planner.get_llm", return_value=planner_llm))

    analyst_llm = MagicMock()
    gold_codes = mock.get("gold_codes", [])
    if gold_codes:
        analyst_llm.invoke.side_effect = [_mock_response(c) for c in gold_codes]
    stack.enter_context(patch("ai_etl.agents.analysis.analyst.get_llm", return_value=analyst_llm))

    science_llm = MagicMock()
    science_codes = mock.get("science_codes", [])
    if science_codes:
        science_llm.invoke.side_effect = [_mock_response(c) for c in science_codes]
    stack.enter_context(patch("ai_etl.agents.analysis.science.get_llm", return_value=science_llm))

    advisor_llm = MagicMock()
    advisor_llm.invoke.return_value = _mock_response(json.dumps(mock["advisor_response"]))
    stack.enter_context(patch("ai_etl.agents.analysis.advisor.get_llm", return_value=advisor_llm))


def _ensure_generated(data: dict[str, Any]) -> Path:
    csv_path: Path = REPO_ROOT / data["path"]
    if not csv_path.exists():
        generator = REPO_ROOT / data["generator"]
        subprocess.run([sys.executable, str(generator)], cwd=REPO_ROOT, check=True)
    return csv_path


def _resolve_data_path(scenario: dict[str, Any]) -> Path:
    data = scenario["data"]
    if data["kind"] == "generated":
        return _ensure_generated(data)
    if data["kind"] == "fixture":
        path: Path = REPO_ROOT / data["path"]
        return path
    raise ValueError(f"Unknown data.kind: {data['kind']!r}")


def scenario_metrics_from_result(
    scenario_id: str,
    category: str,
    expect_failure: bool,
    data_source: str,
    elapsed_ms: float,
    state: dict[str, Any],
    gold_results: list[dict[str, Any]],
    science_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure aggregation of one scenario's outcome into the flat metrics shape
    `compare_against_baseline` consumes. No I/O — takes already-computed results."""
    status = state.get("status", "unknown")
    status_matches_expectation = (status == "failed") if expect_failure else (status == "completed")

    sanity_warnings = 0
    for result in (*gold_results, *science_results):
        if result.get("error") is not None:
            continue
        sanity = result.get("sanity_check")
        if sanity and sanity.get("severity") not in (None, "ok"):
            sanity_warnings += 1

    return {
        "scenario_id": scenario_id,
        "category": category,
        "data_source": data_source,
        "status": status,
        "expect_failure": expect_failure,
        "status_matches_expectation": status_matches_expectation,
        "quality_score": score_quality(state) if not expect_failure else None,
        "sanity_warnings": sanity_warnings,
        "elapsed_ms": round(elapsed_ms, 1),
    }


def run_scenario(scenario: dict[str, Any], data_source: str, tmp_dir: Path) -> dict[str, Any]:
    """Run one scenario end-to-end through `run_full_analysis`. I/O-heavy orchestration —
    exercised manually/via CI, not unit-tested directly (same boundary Sprint 8's own
    `run_scenario1` draws — see its module docstring)."""
    csv_path = _resolve_data_path(scenario)
    run_dir = tmp_dir / scenario["id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "output.csv"

    spec = scenario["spec_template"].format(csv_path=str(csv_path), output_path=str(output_path))
    mock = scenario["mock"]
    plan = {
        "sources": [{"name": "data", "type": "csv", "path": str(csv_path)}],
        "destination": {"type": "csv", "path": str(output_path)},
        "transformations": mock["transformations"],
        "quality_checks": ["null_check", "duplicate_check", "outlier_check"],
    }

    start = time.perf_counter()
    with ExitStack() as stack:
        if data_source != "real":
            _patch_mocked_pipeline(stack, plan, mock)
        _patch_no_audit_persistence(stack)

        result = pipeline_service.run_full_analysis(
            spec=spec,
            business_question=scenario.get("business_question", ""),
            run_dir=str(run_dir),
            tenant_id=f"sprint28_{uuid.uuid4().hex[:8]}",
        )
    elapsed_ms = (time.perf_counter() - start) * 1000

    return scenario_metrics_from_result(
        scenario_id=scenario["id"],
        category=scenario["category"],
        expect_failure=scenario.get("expect_failure", False),
        data_source=data_source,
        elapsed_ms=elapsed_ms,
        state=cast(dict[str, Any], result["state"]),
        gold_results=cast(list[dict[str, Any]], list(result["gold"])),
        science_results=cast(list[dict[str, Any]], list(result["science"])),
    )


def aggregate_report(per_scenario: list[dict[str, Any]], data_source: str) -> dict[str, Any]:
    """Pure aggregation over already-computed per-scenario metrics. Unit-tested directly."""
    scores = [m["quality_score"] for m in per_scenario if m["quality_score"] is not None]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": data_source,
        "n_scenarios": len(per_scenario),
        "mean_quality_score": round(statistics.mean(scores), 2) if scores else None,
        "min_quality_score": round(min(scores), 2) if scores else None,
        "total_sanity_warnings": sum(m["sanity_warnings"] for m in per_scenario),
        "scenarios_failing_expectation": sorted(
            m["scenario_id"] for m in per_scenario if not m["status_matches_expectation"]
        ),
        "per_scenario": per_scenario,
    }


def compare_against_baseline(
    current: dict[str, Any],
    baseline: dict[str, Any],
    quality_tolerance: float = DEFAULT_QUALITY_TOLERANCE,
) -> dict[str, Any]:
    """Pure regression-detection function — no I/O, no LLM. This is the literal target of
    ADR-029/Sprint 28's definition of done: a deliberately worse mocked run's metrics fed
    through this function must be flagged. See tests/unit/test_regression_harness.py."""
    regressions: list[str] = []

    b_mean, c_mean = baseline.get("mean_quality_score"), current.get("mean_quality_score")
    if b_mean is not None and c_mean is not None and c_mean < b_mean - quality_tolerance:
        regressions.append(
            f"mean_quality_score regressed: {b_mean} -> {c_mean} (tolerance {quality_tolerance})"
        )

    b_min, c_min = baseline.get("min_quality_score"), current.get("min_quality_score")
    if b_min is not None and c_min is not None and c_min < b_min - quality_tolerance:
        regressions.append(
            f"min_quality_score regressed: {b_min} -> {c_min} (tolerance {quality_tolerance})"
        )

    b_warn = baseline.get("total_sanity_warnings", 0)
    c_warn = current.get("total_sanity_warnings", 0)
    if c_warn > b_warn:
        regressions.append(f"sanity-check warnings increased: {b_warn} -> {c_warn}")

    new_failing = set(current.get("scenarios_failing_expectation", [])) - set(
        baseline.get("scenarios_failing_expectation", [])
    )
    if new_failing:
        regressions.append(f"scenarios newly failing their expected status: {sorted(new_failing)}")

    baseline_by_id = {m["scenario_id"]: m for m in baseline.get("per_scenario", [])}
    for m in current.get("per_scenario", []):
        b = baseline_by_id.get(m["scenario_id"])
        if not b:
            continue
        b_score, c_score = b.get("quality_score"), m.get("quality_score")
        if b_score is not None and c_score is not None and c_score < b_score - quality_tolerance:
            regressions.append(
                f"scenario '{m['scenario_id']}' quality_score regressed: {b_score} -> {c_score}"
            )

    return {"passed": len(regressions) == 0, "regressions": regressions}


def load_scenarios(filter_ids: list[str] | None = None) -> list[dict[str, Any]]:
    scenarios = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        scenario = json.loads(path.read_text())
        if filter_ids is None or scenario["id"] in filter_ids:
            scenarios.append(scenario)
    return scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios", default=None, help="Comma-separated scenario ids to run (default: all)."
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite the committed baseline with this run's metrics instead of comparing "
        "against it. A deliberate human decision after reviewing the new run's output.",
    )
    parser.add_argument(
        "--quality-tolerance",
        type=float,
        default=DEFAULT_QUALITY_TOLERANCE,
        help=f"score_quality drop tolerance before flagging a regression (default: "
        f"{DEFAULT_QUALITY_TOLERANCE}).",
    )
    args = parser.parse_args()

    filter_ids = [s.strip() for s in args.scenarios.split(",")] if args.scenarios else None
    scenarios = load_scenarios(filter_ids)
    if not scenarios:
        print("[regression_harness] no scenarios matched — nothing to run.")
        sys.exit(1)

    data_source = "real" if os.environ.get("OPENAI_API_KEY") else "mock"
    print(f"[regression_harness] data_source={data_source} n_scenarios={len(scenarios)}")
    if data_source != "real":
        print(
            "[regression_harness] NOTE: no OPENAI_API_KEY — running with mocked, fixed LLM "
            "responses. This validates the harness's own deterministic code (extraction, "
            "sandbox execution, output_validation, this comparison logic) but CANNOT catch a "
            "real prompt-wording regression. Re-run with OPENAI_API_KEY exported for that."
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_dir = RESULTS_DIR / "_runs"
    tmp_dir.mkdir(exist_ok=True)

    per_scenario = []
    for scenario in scenarios:
        metrics = run_scenario(scenario, data_source, tmp_dir)
        per_scenario.append(metrics)
        print(
            f"  {scenario['id']}: status={metrics['status']} "
            f"expected_ok={metrics['status_matches_expectation']} "
            f"quality_score={metrics['quality_score']} "
            f"sanity_warnings={metrics['sanity_warnings']}"
        )

    report = aggregate_report(per_scenario, data_source)
    LATEST_RUN_PATH.write_text(json.dumps(report, indent=2))
    print(f"[regression_harness] wrote {LATEST_RUN_PATH}")

    if args.update_baseline:
        BASELINE_PATH.write_text(json.dumps(report, indent=2))
        print(f"[regression_harness] baseline updated: {BASELINE_PATH}")
        return

    if not BASELINE_PATH.exists():
        print("[regression_harness] no baseline found — writing this run as the initial baseline.")
        BASELINE_PATH.write_text(json.dumps(report, indent=2))
        return

    baseline = json.loads(BASELINE_PATH.read_text())
    comparison = compare_against_baseline(report, baseline, args.quality_tolerance)
    if comparison["passed"]:
        print("[regression_harness] PASSED — no regression vs. baseline.")
    else:
        print("[regression_harness] FAILED — regression(s) detected vs. baseline:")
        for r in comparison["regressions"]:
            print(f"  - {r}")
    sys.exit(0 if comparison["passed"] else 1)


if __name__ == "__main__":
    main()
