"""Audit persistence — saves pipeline run state to JSON and the application Postgres.

Split (Sprint 33) out of a single ~1370-line `audit/db.py` god file into 5
modules by responsibility, re-exported here so every existing
`from ai_etl.audit.db import X` call site outside `audit/` keeps working
unchanged:

- `runs.py` — `ensure_user`, Silver run persistence (`save_run`) and
  Gold/Science/Advisor analysis persistence (`save_analysis`), their JSON/
  reload helpers, and `save_stage_latencies` (ADR-007).
- `pipelines.py` — saved-pipeline CRUD, scheduling claims
  (`claim_due_pipeline`/`release_pipeline_claim`), write-approval
  (`mark_pipeline_approved`, `list_pending_approvals`), per-pipeline
  LLM provider/model overrides (Sprint 30, ADR-031), and per-pipeline
  notification destination overrides (Sprint 37, ADR-034).
- `health.py` — health-snapshot cache (`record_pipeline_health`,
  `get_pipeline_health`), run-history time series, and the drift-detection
  lookup (`get_previous_completed_run`).
- `budget.py` — tenant monthly budget cap (Sprint 29, ADR-019); pre-run cost
  estimation's historical-average helpers (Sprint 35).
- `onboarding.py` — self-serve activation checklist (Sprint 26, ADR-027).
- `retention.py` — per-tenant automatic run-retention policy config
  (Sprint 36, ADR-035), same shape as `budget.py`'s spend cap.
- `locale.py` — per-tenant locale config (Sprint 25, ADR-036), same shape as
  `retention.py`'s single scalar column.

Pure reorganization — no behavior change. Internal call sites within
`audit/` should import directly from the specific submodule; everything
else should keep importing from `ai_etl.audit.db` as before.
"""

from ai_etl.audit.db.budget import (
    get_avg_run_cost_usd,
    get_global_avg_run_cost_usd,
    get_monthly_budget,
    get_monthly_spend_usd,
    set_monthly_budget,
)
from ai_etl.audit.db.health import (
    DEFAULT_PIPELINE_HEALTH_WINDOW,
    DEFAULT_PIPELINE_HISTORY_LIMIT,
    get_pipeline_health,
    get_previous_completed_run,
    list_pipeline_run_history,
    record_pipeline_health,
)
from ai_etl.audit.db.locale import get_locale, set_locale
from ai_etl.audit.db.onboarding import get_onboarding_status
from ai_etl.audit.db.pipelines import (
    claim_due_pipeline,
    create_saved_pipeline,
    get_run_status_and_pipeline,
    get_saved_pipeline,
    get_saved_pipeline_llm_config,
    get_saved_pipeline_notification_config,
    get_saved_pipeline_notification_target,
    list_due_pipelines,
    list_pending_approvals,
    list_saved_pipelines,
    mark_pipeline_approved,
    record_pipeline_run,
    release_pipeline_claim,
    set_saved_pipeline_llm_config,
    set_saved_pipeline_notification_config,
    update_saved_pipeline,
)
from ai_etl.audit.db.retention import (
    RetentionPolicy,
    get_retention_days,
    list_tenants_with_retention,
    set_retention_days,
)
from ai_etl.audit.db.runs import (
    ensure_user,
    load_full_result,
    load_history,
    save_analysis,
    save_run,
    save_stage_latencies,
)

__all__ = [
    "DEFAULT_PIPELINE_HEALTH_WINDOW",
    "DEFAULT_PIPELINE_HISTORY_LIMIT",
    "RetentionPolicy",
    "claim_due_pipeline",
    "create_saved_pipeline",
    "ensure_user",
    "get_avg_run_cost_usd",
    "get_global_avg_run_cost_usd",
    "get_locale",
    "get_monthly_budget",
    "get_monthly_spend_usd",
    "get_onboarding_status",
    "get_pipeline_health",
    "get_previous_completed_run",
    "get_retention_days",
    "get_run_status_and_pipeline",
    "get_saved_pipeline",
    "get_saved_pipeline_llm_config",
    "get_saved_pipeline_notification_config",
    "get_saved_pipeline_notification_target",
    "list_due_pipelines",
    "list_pending_approvals",
    "list_pipeline_run_history",
    "list_saved_pipelines",
    "list_tenants_with_retention",
    "load_full_result",
    "load_history",
    "mark_pipeline_approved",
    "record_pipeline_health",
    "record_pipeline_run",
    "release_pipeline_claim",
    "save_analysis",
    "save_run",
    "save_stage_latencies",
    "set_locale",
    "set_monthly_budget",
    "set_retention_days",
    "set_saved_pipeline_llm_config",
    "set_saved_pipeline_notification_config",
    "update_saved_pipeline",
]
