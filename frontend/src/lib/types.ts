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
  [key: string]: unknown;
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

/** `GET /config` — read-only, which model every agent in a run currently uses. */
export type ApiConfig = {
  model_name: string;
};
