/**
 * Sprint 7 — static content mirroring what `app.py`'s sidebar used to explain
 * (pyramid + "how it works" + per-agent descriptions), plus the phase
 * grouping the live progress stepper (`<AgentProgress>`) highlights against
 * `GET /runs/{task_id}/status`'s `meta.stage` (Sprint 7 backend addition,
 * `services/execution_queue.py`). Kept as a hand-written TS mirror of
 * `services/pipeline_service.py::AGENT_STEPS` and its inline stage strings
 * ("silver" | "planner" | "gold:*"/"science:*" | "advisor") rather than a new
 * `/agents` endpoint — this content is static across deploys, not runtime
 * config, so duplicating it costs less than a network round-trip.
 */

export type AgentInfo = {
  emoji: string;
  name: string;
  description: string;
};

export const PYRAMID: { emoji: string; label: string; description: string }[] = [
  { emoji: "🥉", label: "Bronze", description: "Dado bruto, sem nenhum toque." },
  { emoji: "🥈", label: "Silver", description: "Limpo, padronizado, validado." },
  { emoji: "🏅", label: "Gold", description: "KPIs e insights descritivos." },
  { emoji: "🔬", label: "Science", description: "Previsões e modelos preditivos." },
  { emoji: "🎯", label: "Advisor", description: "Recomendações de ação." },
];

/** Mirrors `services/pipeline_service.py::AGENT_STEPS` (Silver/LangGraph
 * nodes) plus the four Agentic BI agents that run after Silver. */
export const AGENTS: AgentInfo[] = [
  {
    emoji: "🧠",
    name: "Orchestrator",
    description: "Lê a especificação e planeja o pipeline (fontes, transformações, destino).",
  },
  {
    emoji: "📥",
    name: "Extractor",
    description: "Extrai e inspeciona os dados de cada fonte (CSV, Postgres, REST, PDF/DOCX).",
  },
  {
    emoji: "⚙️",
    name: "Transformer",
    description: "Gera e executa (em sandbox) o código de limpeza/transformação — a camada Silver.",
  },
  {
    emoji: "🔍",
    name: "Quality",
    description: "Verifica nulos, duplicatas e outliers nos dados transformados.",
  },
  {
    emoji: "💾",
    name: "Loader",
    description: "Persiste os dados limpos no destino configurado.",
  },
  {
    emoji: "🧭",
    name: "Planner",
    description: "Decompõe a pergunta de negócio em sub-análises (Gold e/ou Science).",
  },
  {
    emoji: "🤖",
    name: "Analyst (Gold)",
    description: "Gera código pandas, calcula KPIs e cria o gráfico de cada sub-análise descritiva.",
  },
  {
    emoji: "🔬",
    name: "Science Agent",
    description: "Treina um modelo preditivo (scikit-learn) e gera previsões.",
  },
  {
    emoji: "🎯",
    name: "Advisor",
    description: "Sintetiza Gold + Science em recomendações de ação priorizadas.",
  },
];

export type ProgressPhase = "silver" | "planner" | "analysis" | "advisor";

export const PHASES: { key: ProgressPhase; label: string }[] = [
  { key: "silver", label: "Silver" },
  { key: "planner", label: "Planner" },
  { key: "analysis", label: "Gold / Science" },
  { key: "advisor", label: "Advisor" },
];

/** Maps a raw `meta.stage` string (e.g. `"gold:0"`, `"science:1_repair"`) to
 * the coarse phase it belongs to, for the stepper's highlight state. */
export function stageToPhase(stage: string): ProgressPhase | null {
  if (stage === "silver") return "silver";
  if (stage === "planner") return "planner";
  if (stage.startsWith("gold") || stage.startsWith("science")) return "analysis";
  if (stage === "advisor") return "advisor";
  return null;
}
