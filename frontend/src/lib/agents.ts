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
 *
 * Sprint 25 (ADR-036): only structural data (id/emoji/name — proper nouns,
 * identical across locales) stays here now. Translated `label`/`description`
 * text lives in `messages/{locale}.json` under the "agents" namespace
 * (`pyramid`/`roster` arrays, correlated by index with `PYRAMID`/`AGENTS`
 * below), read via `t.raw` at both call sites — `agents-info.tsx` and the
 * landing page (`(marketing)/page.tsx`).
 */

export type AgentInfo = {
  id: string;
  emoji: string;
  name: string;
};

export const PYRAMID: { id: string; emoji: string }[] = [
  { id: "bronze", emoji: "🥉" },
  { id: "silver", emoji: "🥈" },
  { id: "gold", emoji: "🏅" },
  { id: "science", emoji: "🔬" },
  { id: "advisor", emoji: "🎯" },
];

/** Mirrors `services/pipeline_service.py::AGENT_STEPS` (Silver/LangGraph
 * nodes) plus the four Agentic BI agents that run after Silver. `name` is a
 * proper noun (Orchestrator, Extractor, ...), identical in both locales —
 * only the `roster` descriptions in the message catalog are translated. */
export const AGENTS: AgentInfo[] = [
  { id: "orchestrator", emoji: "🧠", name: "Orchestrator" },
  { id: "extractor", emoji: "📥", name: "Extractor" },
  { id: "transformer", emoji: "⚙️", name: "Transformer" },
  { id: "quality", emoji: "🔍", name: "Quality" },
  { id: "loader", emoji: "💾", name: "Loader" },
  { id: "planner", emoji: "🧭", name: "Planner" },
  { id: "analyst_gold", emoji: "🤖", name: "Analyst (Gold)" },
  { id: "science_agent", emoji: "🔬", name: "Science Agent" },
  { id: "advisor", emoji: "🎯", name: "Advisor" },
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
