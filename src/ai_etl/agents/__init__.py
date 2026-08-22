"""Agents package (Sprint 33 reorg) — split into two subpackages.

`ai_etl.agents.pipeline` — the 5 LangGraph nodes wired into the pipeline
graph (`ai_etl.core.graph`). `ai_etl.agents.analysis` — the Agentic BI
functions (Planner/Analyst/Science/Advisor) called directly by `services/`,
outside the graph. `_llm_codegen.py` at this level holds helpers shared by
both subpackages.
"""
