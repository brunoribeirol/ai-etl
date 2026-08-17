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
    [key: string]: unknown;
  };
  gold: AnalysisEntry[];
  science: AnalysisEntry[];
  advisor: AdvisorResult;
  question: string;
  tokens: TokenUsage;
};
