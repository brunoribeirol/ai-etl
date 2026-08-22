"""Agentic BI functions (Sprint 33 reorg) — Planner, Analyst, Science, Advisor.

Each exports a plain function (`plan_analysis_tasks`, `run_analyst`,
`run_science`, `run_advisor`) called directly by `services/pipeline_service.py`
for the "Ask a question about this dataset" flow — none of these are
LangGraph nodes and none run inside the pipeline graph. See
`ai_etl.agents.pipeline` for the 5 nodes that do.
"""
