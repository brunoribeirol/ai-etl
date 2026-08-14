"""LangGraph pipeline graph definition."""

import time
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from ai_etl.agents.extractor import extractor_node
from ai_etl.agents.loader import loader_node
from ai_etl.agents.orchestrator import orchestrator_node
from ai_etl.agents.quality import quality_node
from ai_etl.agents.transformer import transformer_node
from ai_etl.core.state import PipelineState

NodeFn = Callable[[PipelineState], PipelineState]


def _timed(name: str, node_fn: NodeFn) -> NodeFn:
    """Wrap a LangGraph node to record its wall-clock duration into
    `state["stage_durations"]`, without modifying the node's own implementation.

    Per ADR-007, latency capture lives at graph-construction time (here), not
    inside each agent function — `extractor.py`/`transformer.py`/`quality.py`/
    `loader.py`/`orchestrator.py` stay untouched. Merges into the *incoming*
    state's durations (not the node's return value) so this works whether a
    node returns a partial update or a full state copy.
    """

    def _wrapped(state: PipelineState) -> PipelineState:
        start = time.monotonic()
        result = node_fn(state)
        elapsed = time.monotonic() - start

        durations = dict(state.get("stage_durations", {}))
        durations[name] = durations.get(name, 0.0) + elapsed
        return {**result, "stage_durations": durations}

    return _wrapped


def route_after_quality(state: PipelineState) -> str:
    """Route to loader if quality passed; end pipeline if quality blocked."""
    severity = state.get("quality_report", {}).get("severity", "ok")
    if severity == "error":
        return END
    return "loader"


def build_graph() -> Any:
    """Build and compile the AI-ETL LangGraph pipeline.

    Graph topology:
        START → orchestrator → extractor → transformer → quality
        quality ──(ok)──→ loader → END
        quality ──(error)──→ END
    """
    graph: StateGraph[PipelineState] = StateGraph(PipelineState)

    graph.add_node("orchestrator", _timed("orchestrator", orchestrator_node))
    graph.add_node("extractor", _timed("extractor", extractor_node))
    graph.add_node("transformer", _timed("transformer", transformer_node))
    graph.add_node("quality", _timed("quality", quality_node))
    graph.add_node("loader", _timed("loader", loader_node))

    graph.set_entry_point("orchestrator")
    graph.add_edge("orchestrator", "extractor")
    graph.add_edge("extractor", "transformer")
    graph.add_edge("transformer", "quality")
    graph.add_conditional_edges(
        "quality",
        route_after_quality,
        {"loader": "loader", END: END},
    )
    graph.add_edge("loader", END)

    return graph.compile()
