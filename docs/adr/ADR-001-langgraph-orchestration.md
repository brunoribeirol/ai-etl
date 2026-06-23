# ADR-001 — Use LangGraph for multi-agent orchestration

**Status:** Accepted  
**Date:** 2026-06-22  
**Deciders:** Bruno Ribeiro

---

## Context

The framework needs to coordinate 5 specialized agents across a sequential-with-branching pipeline. Requirements:
- Agents must be decoupled — each reads shared state, executes, returns updated state
- The pipeline must branch on quality severity (ok/warning → continue; error → halt)
- State must be typed and inspectable at every step for audit purposes
- Retries must be localizable per-agent without restarting the whole pipeline

Options evaluated:
1. **Raw Python orchestration**: manual state dict, sequential function calls — no built-in graph semantics, no conditional routing, verbose
2. **LangChain AgentExecutor**: designed for ReAct-style single agents, not multi-agent pipelines — routing is awkward
3. **LangGraph**: explicit graph construction, typed state, conditional edges, built-in streaming support

## Decision

Use **LangGraph** (`langgraph>=0.2`) as the orchestration layer.

The graph is a `StateGraph[PipelineState]` with five nodes (one per agent) and one conditional edge from the Quality node: `route_after_quality()` routes to the Loader node on ok/warning, or to `END` on error.

## Consequences

- **Positive**: graph topology is explicit and auditable; adding a new agent is just `add_node` + `add_edge`; conditional routing is a single function returning node names
- **Positive**: `PipelineState` TypedDict enforces a single contract between all nodes
- **Negative**: LangGraph's API evolves quickly — the `StateGraph[T]` generic was added in 0.2; pinning `langgraph>=0.2` is required
- **Negative**: streaming and async support require different invocation patterns (`astream`); not used in v0.1 but will be needed for production

## Related

- [ADR-002](ADR-002-shared-pipelinestate-typeddict.md) — why PipelineState is a TypedDict
