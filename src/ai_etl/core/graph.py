"""LangGraph pipeline graph definition."""

from typing import Any

from langgraph.graph import END, StateGraph

from ai_etl.agents.extractor import extractor_node
from ai_etl.agents.loader import loader_node
from ai_etl.agents.orchestrator import orchestrator_node
from ai_etl.agents.quality import quality_node
from ai_etl.agents.transformer import transformer_node
from ai_etl.core.state import PipelineState


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

    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("extractor", extractor_node)
    graph.add_node("transformer", transformer_node)
    graph.add_node("quality", quality_node)
    graph.add_node("loader", loader_node)

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
