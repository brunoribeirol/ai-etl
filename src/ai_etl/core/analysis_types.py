"""Shared result contracts for the analytical layer (Planner, Gold, Science, Advisor).

These TypedDicts formalize the dict-based contracts these agents already used
informally. Several bugs found in this project were exactly this kind of
undocumented contract drifting between agents (a `None` where a string was
assumed, a stale key name silently returning a default). Formalizing the shape
doesn't add runtime validation — these are still plain dicts at runtime — but it
gets every call site checked by mypy instead of relying on each agent's author
remembering the informal contract.

`fig` is typed as `Any` rather than `plotly.graph_objects.Figure`: plotly is an
optional "app" extra (see pyproject.toml), and this module lives under `core/`,
which the base package must be importable without that extra installed.
"""

from typing import Any, Literal, NotRequired, TypedDict

import pandas as pd

from ai_etl.core.state import PipelineState


class TokenUsage(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class AnalysisTask(TypedDict):
    question: str
    type: Literal["descriptive", "diagnostic_or_predictive"]


class OutputSanityCheckEntry(TypedDict):
    """One entry from `core/output_validation.py` (Sprint 21, ADR-026).

    Mirrors `agents/quality.py`'s per-check dict shape — `check` names the rule,
    `severity` is always "ok" or "warning" for this module (never "error", see
    ADR-026 Decision 1), `detail` is a short human-readable explanation.
    """

    check: str
    severity: Literal["ok", "warning"]
    detail: str


class OutputSanityCheck(TypedDict):
    """Result of sanity-checking one Gold/Science sub-task's output against the
    Silver data it was derived from (Sprint 21, ADR-026). Same three-key shape as
    `PipelineState.quality_report` so the frontend/persistence layers reuse an
    established pattern instead of a new one.
    """

    checks: list[OutputSanityCheckEntry]
    severity: Literal["ok", "warning"]
    summary: str


class GoldResult(TypedDict):
    task_question: str
    gold_df: pd.DataFrame
    fig: Any
    narrative: str
    code: str
    attempts: int
    error: str | None
    tokens: TokenUsage
    # Set by app.py when this result came from a simplified fallback question after
    # the original sub-task question failed outright — not set by run_analyst itself.
    repaired: NotRequired[bool]
    # Set by pipeline_service.py after a successful run (Sprint 21, ADR-026) — absent
    # on a failed sub-task, which has nothing to sanity-check.
    sanity_check: NotRequired[OutputSanityCheck]


class ModelInfo(TypedDict):
    model_type: str
    task: str
    metrics: dict[str, Any]
    features: list[str]
    target: str


class ScienceResult(TypedDict):
    task_question: str
    predictions_df: pd.DataFrame
    fig: Any
    narrative: str
    model_info: "ModelInfo | dict[str, Any]"
    code: str
    attempts: int
    error: str | None
    tokens: TokenUsage
    # Set by app.py when this result came from a simplified fallback question after
    # the original sub-task question failed outright — not set by run_science itself.
    repaired: NotRequired[bool]
    # Set by pipeline_service.py after a successful run (Sprint 21, ADR-026) — absent
    # on a failed sub-task, which has nothing to sanity-check.
    sanity_check: NotRequired[OutputSanityCheck]


class Recommendation(TypedDict):
    action: str
    rationale: str
    priority: Literal["high", "medium", "low"]
    expected_impact: str


class AdvisorResult(TypedDict):
    recommendations: list[Recommendation]
    summary: str
    error: str | None
    tokens: TokenUsage


class AnalysisRunResult(TypedDict):
    """Return contract of `pipeline_service.run_full_analysis` — the full
    Silver -> Planner -> Gold/Science -> Advisor run for one business question.

    `advisor` keeps the legacy "empty dict means not run" sentinel from the code
    this replaces (Silver failing/returning no rows skips Planner/Gold/Science/
    Advisor entirely): an `AdvisorResult` when the pipeline reached the Advisor,
    or `{}` when it didn't. Callers must check truthiness, not just read fields.
    """

    state: PipelineState
    gold: list[GoldResult]
    science: list[ScienceResult]
    advisor: "AdvisorResult | dict[str, Any]"
    question: str
    tokens: TokenUsage
