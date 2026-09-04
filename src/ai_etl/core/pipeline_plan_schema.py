"""Pydantic schema for `pipeline_plan` — the Orchestrator's LLM-generated JSON output.

Wave 0 security fix (2026-08-24 audit, Red Team CRITICAL finding): `pipeline_plan` was
previously typed only as `dict[str, Any]` (see `core/state.py`'s comment-only schema)
and never structurally validated before `agents/pipeline/extractor.py` read fields off
of it — an adversarial spec could get the Orchestrator's LLM to emit unexpected keys
with nothing catching it before execution.

This model validates structure (each source has `name`/`type`, `type` is one of the
types `ORCHESTRATOR_PROMPT` itself documents) while staying permissive on per-type
fields (`extra="allow"`) — full per-type field validation (REST auth shapes, etc.) is a
larger follow-up, not needed to close this specific gap.

**This schema does not validate `query` content** — the actual SQL-injection defense for
`sqlite`/`mysql` custom `query` strings lives at the connector level
(`sources/sqlite_source.py`/`sources/mysql_source.py`'s `validate_select_only_query`,
mirroring `sources/mongodb_source.py::_validate_query`'s connector-level pattern) so it
runs regardless of caller, not just when a plan happens to pass through the Orchestrator.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

_SourceType = Literal["csv", "postgres", "sqlite", "mysql", "mongodb", "rest", "document"]
_DestinationType = Literal["csv", "postgres", "mysql", "mongodb", "s3_parquet"]


class PipelinePlanSource(BaseModel):
    """One entry in `pipeline_plan["sources"]`.

    Extra per-type fields (`path`, `table`, `query`, `url`, `auth`, ...) pass through
    unvalidated here — validated where they're actually used (the relevant connector).
    """

    model_config = ConfigDict(extra="allow")

    name: str
    type: _SourceType


class PipelinePlanDestination(BaseModel):
    """`pipeline_plan["destination"]` — extra per-type fields pass through unvalidated."""

    model_config = ConfigDict(extra="allow")

    type: _DestinationType


class PipelinePlan(BaseModel):
    """Top-level shape of `pipeline_plan`, matching `ORCHESTRATOR_PROMPT`'s documented
    schema (`agents/pipeline/orchestrator.py`). Used only as a validation gate — callers
    keep working with the original `dict` (see `orchestrator_node`), not this model's
    own `model_dump()`, so plan shape passed to `extractor_node` is unchanged.
    """

    model_config = ConfigDict(extra="allow")

    sources: list[PipelinePlanSource] = Field(default_factory=list)
    destination: Optional[PipelinePlanDestination] = None
    transformations: list[str] = Field(default_factory=list)
    quality_checks: list[str] = Field(default_factory=list)
