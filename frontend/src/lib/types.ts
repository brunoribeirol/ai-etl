/**
 * Response shapes from `src/ai_etl/api/` (ADR-011). Mirrors
 * `api/serialization.py`/`api/routers/runs.py` — kept in sync by hand since
 * there's no shared schema generation between the Python and TS sides yet.
 */

export type RunSummary = {
  run_id: string;
  status: string;
  rows_loaded: number | null;
  timestamp: string;
  spec: string;
  cost_usd: number | null;
  model_name: string | null;
};

/** `GET /runs/pending-approval` (Sprint 27, ADR-028) — the operator's
 * work-queue of gated writes, most recently created first. Mirrors
 * `audit/db/pipelines.py::list_pending_approvals`. */
export type PendingApproval = {
  run_id: string;
  spec: string;
  timestamp: string;
  saved_pipeline_id: string | null;
  pipeline_name: string | null;
};

/** `PipelineState["load_preview"]` (Sprint 27, ADR-028) — what a gated
 * write would do, computed without ever writing. `destination_type`-specific
 * `existing` shape (mirrors `destinations/*.py::preview_*`). */
export type LoadPreview = {
  destination_type: "csv" | "postgres" | "s3_parquet";
  destination: string;
  would_write_rows: number;
  existing: Record<string, number> | null;
};

export type TokenUsage = {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
};

export type PlotlyFigure = {
  data: unknown[];
  layout: Record<string, unknown>;
};

export type AnalysisEntry = {
  task_question?: string;
  narrative?: string;
  attempts?: number;
  error?: string | null;
  repaired?: boolean;
  tokens?: TokenUsage;
  model_info?: Record<string, unknown>;
  data_preview?: Record<string, unknown>[];
  data_shape?: [number, number];
  data_path?: string;
  fig_path?: string;
  gold_df?: Record<string, unknown>[];
  predictions_df?: Record<string, unknown>[];
  fig?: PlotlyFigure;
  /** Sprint 7 — generated pandas/sklearn source (`agents/analyst.py`/
   * `science.py`), now persisted by `_serialize_analysis_result`. Optional:
   * absent on any run saved before this field existed. */
  code?: string | null;
  /** Sprint 21 (ADR-026) — deterministic sanity-check of this sub-task's own
   * result (`gold_df`/`predictions_df`) against the Silver data it was derived
   * from. Absent on any run saved before this field existed, or on a failed
   * sub-task (nothing to sanity-check). `"error"` is kept for shape-parity with
   * `QualityCheck` even though `core/output_validation.py` never emits it. */
  sanity_check?: {
    checks: {
      check: string;
      severity: "ok" | "warning" | "error";
      detail: string;
    }[];
    severity: "ok" | "warning" | "error";
    summary: string;
  };
};

/** Sprint 7 — per-check entry inside `state.quality_report.checks`
 * (`core/state.py`'s `PipelineState.quality_report` docstring). */
export type QualityCheck = {
  check: string;
  column?: string;
  severity: "ok" | "warning" | "error";
  null_ratio?: number;
  count?: number;
  outlier_count?: number;
  // Sprint 16 (ADR-023) — present only on `check === "custom_rule"` entries
  // (operator-defined quality rules, `agents/quality.py::_check_custom_rules`).
  rule_name?: string;
  operator?: string;
  value?: unknown;
  violation_count?: number;
  error?: string;
  [key: string]: unknown;
};

/** Sprint 16 (ADR-023) — one operator-defined quality rule, mirrors
 * `api/routers/pipelines.py::QualityRule`. */
export type QualityRuleOperator = "not_null" | "gte" | "lte" | "gt" | "lt" | "eq" | "ne";

export type QualityRule = {
  column: string;
  operator: QualityRuleOperator;
  value?: number | string | boolean | null;
  severity?: "ok" | "warning" | "error";
  name?: string | null;
};

export type AdvisorRecommendation = {
  action: string;
  rationale: string;
  priority: "high" | "medium" | "low";
  expected_impact?: string;
};

export type AdvisorResult = {
  recommendations: AdvisorRecommendation[];
  summary: string | null;
  error: string | null;
  tokens?: TokenUsage;
};

export type FullResult = {
  bronze: null;
  state: {
    run_id?: string;
    status?: string;
    spec?: string;
    error?: string | null;
    transformed_data?: Record<string, unknown>[];
    /** Transformer's generated code (`core/state.py::PipelineState.transformation_code`). */
    transformation_code?: string;
    transformation_attempts?: number;
    transformation_error?: string | null;
    pipeline_plan?: Record<string, unknown>;
    quality_report?: { checks?: QualityCheck[]; severity?: string; summary?: string };
    /** Sprint 27 (ADR-028) — set instead of `load_result` when a write is
     * gated; `null`/absent for an ungated or already-resolved run. */
    load_preview?: LoadPreview | null;
    /** Wall-clock seconds per Silver LangGraph node — the field the old
     * Streamlit sidebar meant to read as `_agent_timings` (a key that was
     * never actually set on `state`, a pre-existing dead-code bug; the real
     * field has always been `stage_durations`). */
    stage_durations?: Record<string, number>;
    [key: string]: unknown;
  };
  gold: AnalysisEntry[];
  science: AnalysisEntry[];
  advisor: AdvisorResult;
  question: string;
  tokens: TokenUsage;
};

/** Sprint 7 — `GET /runs/{task_id}/status` response shape, extended with the
 * live-progress `meta` field (`services/execution_queue.py::get_task_status`). */
export type TaskStatus = {
  state: string;
  ready: boolean;
  result: { run_id?: string; status?: string; error?: string | null } | null;
  error: string | null;
  meta: { stage: string; message: string } | null;
};

/** `GET /config` — read-only, which model every agent in a run currently uses.
 * `role` (Wave 6, 2026-08-25 admin panel/approval-gate UI plan) — the
 * caller's resolved viewer/editor/admin role (`api/deps.py::AuthContext`),
 * used only for cosmetic UI decisions (e.g. showing the "Admin" nav link);
 * every actual admin/approval endpoint still independently enforces its own
 * role server-side. */
export type ApiConfig = {
  model_name: string;
  role: "viewer" | "editor" | "admin";
};

/** `GET /admin/tenants` (ADR-032 + Wave 6 gap-closing addition) — the whole
 * tenant directory; `tenant_id` doubles as the raw Clerk user/org id, no
 * separate display name exists. */
export type AdminTenantSummary = {
  tenant_id: string;
  created_at: string;
};

/** `GET /admin/audit-log` — mirrors `audit/admin_log.py::AdminActionRecord`. */
export type AdminActionRecord = {
  id: string;
  actor_user_id: string;
  action: string;
  target_tenant_id: string | null;
  detail: string | null;
  created_at: string;
};

/** `GET /budget`, `GET /admin/tenants/{id}/budget` — mirrors
 * `services/execution_queue.py::BudgetStatus`. */
export type BudgetStatus = {
  cap_usd: number | null;
  spent_usd: number;
  ratio: number | null;
  near_limit: boolean;
  exceeded: boolean;
};

/** `POST /secrets`, `DELETE /secrets/{name}` (Sprint 19, ADR-022) — mirrors
 * `api/routers/secrets.py`'s `{"name": ..., "status": "stored" | "deleted"}`.
 * `GET /secrets` returns `string[]` (names only) directly, no wrapper type
 * needed. Never carries a secret's decrypted value — the router itself never
 * returns one. */
export type SecretMutationResult = {
  name: string;
  status: "stored" | "deleted";
};

/**
 * Sprint 13 (ADR-016) — `/pipelines` CRUD. Mirrors
 * `audit/db.py::_saved_pipeline_row_to_dict`. `source_type` is restricted to
 * "live" connectors only (no browser-uploaded csv/document) — see ADR-016
 * Decision 3.
 */
export type SchedulableSourceType = "postgres" | "sqlite" | "mysql" | "mongodb" | "rest";

export const SCHEDULABLE_SOURCE_TYPES: SchedulableSourceType[] = [
  "postgres",
  "sqlite",
  "mysql",
  "mongodb",
  "rest",
];

export type SavedPipeline = {
  id: string;
  tenant_id: string;
  name: string;
  source_type: SchedulableSourceType;
  spec: string;
  business_question: string;
  cron_schedule: string;
  is_active: boolean;
  next_run_at: string;
  last_task_id: string | null;
  last_run_at: string | null;
  // Sprint 15 (ADR-020) — health-snapshot cache (`consecutive_failures`/
  // `last_status`/`last_error`, persisted on `saved_pipelines`) plus
  // aggregated fields the API computes on read (`success_rate`/
  // `avg_latency_seconds`/`health_sample_size`, `null` until the pipeline
  // has fired at least once — never a fabricated 0).
  consecutive_failures: number;
  last_status: "completed" | "failed" | null;
  last_error: string | null;
  success_rate: number | null;
  avg_latency_seconds: number | null;
  health_sample_size: number;
  // Sprint 16 (ADR-023) — operator-defined quality rules, run on every subsequent
  // execution of this pipeline alongside the fixed checks.
  quality_rules: QualityRule[];
  // Sprint 30 (ADR-031) — per-pipeline LLM provider/model override, merged onto
  // every `saved_pipeline` dict by `api/routers/pipelines.py::_with_health`.
  // Both `null` means "no override, uses this deployment's global default".
  llm_provider: string | null;
  llm_model: string | null;
  created_at: string;
  updated_at: string;
};

/**
 * Sprint 17 (ADR-017) — `GET /pipelines/{id}/history` response entry. Mirrors
 * `audit/db.py::list_pipeline_run_history`. One entry per execution of a
 * saved pipeline, oldest first. `cost_usd`/`model_name`/`total_tokens`/
 * `gold_subtasks`/`science_subtasks` are `null` for a Silver-only fire (no
 * `business_question` set), same "no analysis, no cost" semantics as
 * `RunSummary`.
 */
export type PipelineRunHistoryEntry = {
  run_id: string;
  status: string;
  rows_loaded: number | null;
  timestamp: string;
  error: string | null;
  cost_usd: number | null;
  model_name: string | null;
  total_tokens: number | null;
  gold_subtasks: number | null;
  science_subtasks: number | null;
};

/**
 * Sprint 26 (ADR-027) — `GET /onboarding/status` response. Mirrors
 * `audit/db.py::get_onboarding_status`, derived on read from
 * `runs`/`saved_pipelines`, no dedicated table.
 */
export type OnboardingStatus = {
  run_count: number;
  completed_run_count: number;
  has_completed_run: boolean;
  saved_pipeline_count: number;
  has_saved_pipeline: boolean;
};

/** `GET /tenant/retention` (Sprint 36, ADR-035) — the tenant's automatic
 * run-artifact retention window, if any. Mirrors
 * `audit/db/retention.py::RetentionPolicy`. `retention_days: null` is the
 * default for every tenant ("keep forever") until they opt in via
 * `PATCH /tenant/retention`. */
export type RetentionPolicy = {
  tenant_id: string;
  retention_days: number | null;
};

/** `GET /tenant/export` (Sprint 36, ADR-035) — a tenant's full self-service
 * data-access export. Mirrors `services/tenant_export_service.py::TenantExport`.
 * Row shapes are intentionally untyped (`Record<string, unknown>`) — this is
 * a raw dump of DB rows for download, not data this UI renders field-by-field.
 * `tenant_secrets` is metadata only (id/name/timestamps), never `ciphertext`.
 * `storage_artifacts` is `{run_id, key}` pairs, never inline file bytes
 * (ADR-035 Decision 1). */
export type TenantExport = {
  tenant_id: string;
  exported_at: string;
  user: Record<string, unknown>;
  runs: Record<string, unknown>[];
  analysis_runs: Record<string, unknown>[];
  stage_latencies: Record<string, unknown>[];
  saved_pipelines: Record<string, unknown>[];
  tenant_secrets: Record<string, unknown>[];
  storage_artifacts: Record<string, unknown>[];
};
