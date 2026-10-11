"""Case-study runner over the public datasets — produces the raw evidence only.

Runs each scenario N independent times per model through
`ai_etl.services.pipeline_service.run_full_analysis` (the same entry point a real
production run uses) and writes what happened: one JSON per run, one CSV row per
run, and a per-(scenario, model) summary of counts and means. It does not
interpret anything — conclusions belong to the author of the report.

Scenarios (specs in `case_study/pipelines/public_scenario*`):
    1  CSV -> CSV                          rename, cast, filter, deduplicate
    2  CSV + PostgreSQL -> PostgreSQL      join, filter, aggregate
    3  CSV + PostgreSQL + REST -> PostgreSQL   join, enrich with daily weather
    4  scenario 1 + a business question    Planner -> Analyst/Science -> Advisor

Real calls only
---------------
Every run here calls a real model. A model whose provider credential is missing
is recorded as `skipped` with the reason — never replaced by a mocked response,
because a mocked run measures the surrounding code, not the system under study.
(`model_comparison.py` and `regression_harness.py` do offer a mocked mode; this
script deliberately does not.)

Prerequisites
-------------
    uv run python case_study/data/fetch_public_data.py       # data + manifest
    uv run python case_study/data/seed_public_postgres.py    # scenarios 2 and 3
    POSTGRES_URL and a provider credential (OPENAI_API_KEY / ANTHROPIC_API_KEY) set

Scenario 4 builds plotly figures inside the sandbox, which hangs on macOS (see
CLAUDE.md) — run it on Linux, e.g. through `.github/workflows/case-study.yml`.

Usage
-----
    uv run python case_study/scripts/run_case_study.py
    uv run python case_study/scripts/run_case_study.py --scenarios 1,2 --runs 3
    uv run python case_study/scripts/run_case_study.py --models gpt-4o-mini,anthropic:claude-haiku-5-5
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import platform
import statistics
import sys
import time
import uuid
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINES_DIR = REPO_ROOT / "case_study" / "pipelines"
DATA_DIR = REPO_ROOT / "case_study" / "data"
RESULTS_ROOT = REPO_ROOT / "case_study" / "results" / "public"
MANIFEST_PATH = REPO_ROOT / "case_study" / "public_data_manifest.json"

DEFAULT_RUNS = 5  # same convention as case_study/results/tabela-resultados.md
DEFAULT_MODELS = ["gpt-4o-mini"]

# Reuse Sprint 8's helpers instead of duplicating them — same loading technique as
# regression_harness.py, since case_study/scripts/ is not an installed package.
_MC_PATH = REPO_ROOT / "case_study" / "scripts" / "model_comparison.py"
_mc_spec = importlib.util.spec_from_file_location("model_comparison", _MC_PATH)
assert _mc_spec is not None and _mc_spec.loader is not None
model_comparison = importlib.util.module_from_spec(_mc_spec)
sys.modules.setdefault("model_comparison", model_comparison)
_mc_spec.loader.exec_module(model_comparison)
score_quality = model_comparison.score_quality
provider_and_access = model_comparison._provider_and_access
patch_no_audit_persistence = model_comparison._patch_no_audit_persistence

from ai_etl.core.analysis_types import TokenUsage  # noqa: E402
from ai_etl.core.pricing import compute_cost_usd  # noqa: E402
from ai_etl.services import pipeline_service  # noqa: E402


@dataclass(frozen=True)
class Scenario:
    scenario_id: int
    name: str
    spec_file: str
    question_file: str | None = None
    needs_postgres: bool = False

    def spec(self) -> str:
        return (PIPELINES_DIR / self.spec_file).read_text().strip()

    def question(self) -> str:
        if self.question_file is None:
            return ""
        return (PIPELINES_DIR / self.question_file).read_text().strip()


SCENARIOS: dict[int, Scenario] = {
    1: Scenario(1, "CSV -> CSV", "public_scenario1_spec.txt"),
    2: Scenario(
        2, "CSV + PostgreSQL -> PostgreSQL", "public_scenario2_spec.txt", needs_postgres=True
    ),
    3: Scenario(
        3,
        "CSV + PostgreSQL + REST -> PostgreSQL",
        "public_scenario3_spec.txt",
        needs_postgres=True,
    ),
    4: Scenario(
        4,
        "CSV -> CSV + business question (analysis layer)",
        "public_scenario1_spec.txt",
        question_file="public_scenario4_question.txt",
    ),
}


@dataclass
class RunRecord:
    scenario: int
    model: str
    provider: str
    run_index: int
    status: str  # completed | failed | awaiting_approval | skipped | crashed
    outcome: str = ""  # classification per case-study.md — see classify_outcome()
    elapsed_ms: float | None = None
    rows_loaded: int | None = None
    quality_severity: str | None = None
    transformation_attempts: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    quality_score: float | None = None
    analysis_subtasks: int | None = None
    analysis_subtasks_ok: int | None = None
    recommendations: int | None = None
    stage_durations: dict[str, float] = field(default_factory=dict)
    note: str = ""

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        stages = row.pop("stage_durations")
        row.update({f"stage_{name}_s": seconds for name, seconds in stages.items()})
        return row


def classify_outcome(status: str, quality_severity: str | None, error: str | None) -> str:
    """Map one run onto the four outcome classes defined in the case-study protocol.

    - complete_success:      ran end to end with no human intervention
    - partial_success:       ran, but the Quality agent blocked the load (severity
                             "error") — the designed behaviour, not a malfunction
    - recoverable_failure:   stopped with a recorded, explained error (typically the
                             Transformer exhausting its attempts)
    - unrecoverable_failure: the runner itself caught an unhandled exception
    """
    if status == "completed":
        return "complete_success"
    if status == "crashed":
        return "unrecoverable_failure"
    if quality_severity == "error":
        return "partial_success"
    if error:
        return "recoverable_failure"
    return "unrecoverable_failure"


def _analysis_block(result: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe view of the analysis layer's output: everything except the
    DataFrames and plotly figures, which are not serialisable and not evidence."""

    def subtask(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "question": entry.get("task_question"),
            "error": entry.get("error"),
            "attempts": entry.get("attempts"),
            "narrative": entry.get("narrative"),
            "code": entry.get("code"),
            "sanity_check": entry.get("sanity_check"),
            "model_info": entry.get("model_info"),
        }

    advisor = result.get("advisor") or {}
    return {
        "gold": [subtask(entry) for entry in result.get("gold") or []],
        "science": [subtask(entry) for entry in result.get("science") or []],
        "advisor": {
            "summary": advisor.get("summary"),
            "error": advisor.get("error"),
            "recommendations": advisor.get("recommendations") or [],
        },
    }


def run_once(
    scenario: Scenario,
    model: str,
    run_index: int,
    out_dir: Path,
    persist_audit: bool,
) -> RunRecord:
    provider, has_access = provider_and_access(model)
    bare_model = model.partition(":")[2] if ":" in model else model
    record = RunRecord(
        scenario=scenario.scenario_id,
        model=bare_model,
        provider=provider,
        run_index=run_index,
        status="skipped",
    )

    if not has_access:
        record.note = f"no credential for provider {provider!r}"
        return record
    if scenario.needs_postgres and not os.getenv("POSTGRES_URL"):
        record.note = "POSTGRES_URL is not set"
        return record
    if not (DATA_DIR / "nyc_taxi_trips.csv").exists():
        record.note = "case_study/data/nyc_taxi_trips.csv missing — run fetch_public_data.py"
        return record

    run_dir = out_dir / "runs" / f"s{scenario.scenario_id}_{bare_model}_{run_index}"
    run_dir.mkdir(parents=True, exist_ok=True)
    spec, question = scenario.spec(), scenario.question()

    saved_env = {key: os.environ.get(key) for key in ("AI_ETL_LLM_MODEL", "AI_ETL_LLM_PROVIDER")}
    os.environ["AI_ETL_LLM_MODEL"] = bare_model
    os.environ["AI_ETL_LLM_PROVIDER"] = provider
    result: dict[str, Any] = {}
    crash: str | None = None
    start = time.perf_counter()
    try:
        with ExitStack() as stack:
            if not persist_audit:
                patch_no_audit_persistence(stack)
            result = dict(
                pipeline_service.run_full_analysis(
                    spec=spec,
                    business_question=question,
                    run_dir=str(run_dir),
                    tenant_id=f"case_study_{uuid.uuid4().hex[:8]}",
                )
            )
    except Exception as exc:  # deliberate catch-all: an unhandled crash is itself a result
        crash = f"{type(exc).__name__}: {exc}"
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    state: dict[str, Any] = dict(result.get("state") or {})
    tokens = cast(TokenUsage, dict(result.get("tokens") or {}))
    quality_report = state.get("quality_report") or {}
    record.status = "crashed" if crash else str(state.get("status", "unknown"))
    record.elapsed_ms = round(elapsed_ms, 1)
    record.rows_loaded = (state.get("load_result") or {}).get("rows_loaded")
    record.quality_severity = quality_report.get("severity")
    record.transformation_attempts = state.get("transformation_attempts")
    record.input_tokens = int(tokens.get("input_tokens", 0))
    record.output_tokens = int(tokens.get("output_tokens", 0))
    record.cost_usd = compute_cost_usd(bare_model, tokens) if tokens else None
    record.quality_score = score_quality(state) if state else None
    record.stage_durations = dict(state.get("stage_durations") or {})
    record.note = crash or str(state.get("error") or "")
    record.outcome = classify_outcome(
        record.status, record.quality_severity, crash or state.get("error")
    )

    analysis = _analysis_block(result)
    if question:
        subtasks = analysis["gold"] + analysis["science"]
        record.analysis_subtasks = len(subtasks)
        record.analysis_subtasks_ok = sum(1 for entry in subtasks if not entry["error"])
        record.recommendations = len(analysis["advisor"]["recommendations"])

    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "record": record.to_row(),
                "spec": spec,
                "business_question": question,
                "pipeline_plan": state.get("pipeline_plan"),
                "transformation_code": state.get("transformation_code"),
                "transformation_error": state.get("transformation_error"),
                "quality_report": quality_report,
                "load_result": state.get("load_result"),
                "audit_log": state.get("audit_log"),
                "error": crash or state.get("error"),
                "tokens": tokens,
                "analysis": analysis if question else None,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    return record


def _stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "mean": round(statistics.mean(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "stdev": round(statistics.stdev(values), 3) if len(values) >= 2 else 0.0,
    }


def summarize(records: list[RunRecord]) -> list[dict[str, Any]]:
    """One entry per (scenario, model): counts and descriptive statistics only."""
    groups: dict[tuple[int, str], list[RunRecord]] = {}
    for record in records:
        groups.setdefault((record.scenario, record.model), []).append(record)

    summary = []
    for (scenario_id, model), group in sorted(groups.items()):
        executed = [r for r in group if r.status != "skipped"]
        completed = [r for r in executed if r.status == "completed"]
        outcomes: dict[str, int] = {}
        for record in executed:
            outcomes[record.outcome] = outcomes.get(record.outcome, 0) + 1
        severities: dict[str, int] = {}
        for record in executed:
            key = record.quality_severity or "none"
            severities[key] = severities.get(key, 0) + 1
        summary.append(
            {
                "scenario": scenario_id,
                "scenario_name": SCENARIOS[scenario_id].name,
                "model": model,
                "runs_requested": len(group),
                "runs_executed": len(executed),
                "runs_completed": len(completed),
                "outcomes": outcomes,
                "quality_severity": severities,
                "elapsed_ms": _stats([r.elapsed_ms for r in completed if r.elapsed_ms is not None]),
                "transformation_attempts": _stats(
                    [
                        float(r.transformation_attempts)
                        for r in completed
                        if r.transformation_attempts is not None
                    ]
                ),
                "rows_loaded": _stats(
                    [float(r.rows_loaded) for r in completed if r.rows_loaded is not None]
                ),
                "cost_usd": _stats([r.cost_usd for r in completed if r.cost_usd is not None]),
                "analysis_subtasks_ok": _stats(
                    [
                        float(r.analysis_subtasks_ok)
                        for r in completed
                        if r.analysis_subtasks_ok is not None
                    ]
                ),
                "skipped_reasons": sorted({r.note for r in group if r.status == "skipped"}),
            }
        )
    return summary


def write_outputs(records: list[RunRecord], out_dir: Path, meta: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [record.to_row() for record in records]
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with (out_dir / "runs_log.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    (out_dir / "summary.json").write_text(
        json.dumps({"meta": meta, "summary": summarize(records)}, indent=2, ensure_ascii=False)
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scenarios", default="1,2,3,4", help="Comma-separated scenario ids")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="Runs per scenario/model")
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated models; prefix with provider, e.g. anthropic:claude-haiku-5-5",
    )
    parser.add_argument("--label", default="", help="Results subfolder (default: UTC timestamp)")
    parser.add_argument(
        "--persist-audit",
        action="store_true",
        help="Also persist each run to the application database (needs APP_DATABASE_URL)",
    )
    args = parser.parse_args()

    scenario_ids = [int(value) for value in args.scenarios.split(",") if value.strip()]
    unknown = [value for value in scenario_ids if value not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenario id(s): {unknown}")
    models = [value.strip() for value in args.models.split(",") if value.strip()]

    started = datetime.now(timezone.utc)
    out_dir = RESULTS_ROOT / (args.label or started.strftime("%Y-%m-%dT%H%M%SZ"))
    manifest = json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else None

    records: list[RunRecord] = []
    for scenario_id in scenario_ids:
        for model in models:
            for run_index in range(1, args.runs + 1):
                record = run_once(
                    SCENARIOS[scenario_id], model, run_index, out_dir, args.persist_audit
                )
                records.append(record)
                print(
                    f"scenario {scenario_id} | {record.model} | run {run_index}/{args.runs} | "
                    f"{record.status} | {record.elapsed_ms or '-'} ms | {record.note[:80]}"
                )

    meta = {
        "started_at_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platform": f"{platform.system()} {platform.release()} / Python {platform.python_version()}",
        "sandbox_backend": os.getenv("AI_ETL_SANDBOX_BACKEND", "process"),
        "runs_per_scenario": args.runs,
        "models": models,
        "scenarios": scenario_ids,
        "data_manifest": {
            "trips_extract_sha256": manifest["trips"]["extract_sha256"],
            "trips_selection": manifest["trips"]["selection"],
            "zones_sha256": manifest["zones"]["sha256"],
        }
        if manifest
        else None,
    }
    write_outputs(records, out_dir, meta)
    print(f"\nWrote {out_dir.relative_to(REPO_ROOT)}/runs_log.csv and summary.json")

    executed = [r for r in records if r.status != "skipped"]
    if not executed:
        print("No run was executed — every one was skipped (see the notes above).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
