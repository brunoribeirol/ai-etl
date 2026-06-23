# ADR-002 — Use a shared TypedDict as the single pipeline state contract

**Status:** Accepted  
**Date:** 2026-06-22  
**Deciders:** Bruno Ribeiro

---

## Context

With 5 agents sharing state, the design choices are:
1. **Untyped dict**: flexible but no IDE support, no static validation, agents can silently write wrong keys
2. **Pydantic model**: full validation + serialization but LangGraph's native state type is TypedDict; Pydantic requires a compatibility shim
3. **TypedDict**: native LangGraph contract, statically checked by mypy in strict mode, no runtime overhead

## Decision

Use a single `PipelineState` TypedDict defined in `src/ai_etl/core/state.py`.

All agents must:
- Accept `state: PipelineState` as their only argument
- Return `{**state, "field": new_value}` — never mutate state in-place
- Never add keys outside the TypedDict schema

The `initial_state(spec, run_id)` factory initializes all fields to their zero values, ensuring agents can safely read any key without `KeyError`.

## Consequences

- **Positive**: mypy strict mode catches any type mismatch at the field level across all agents
- **Positive**: state snapshots are naturally serializable to JSON (after DataFrame conversion)
- **Positive**: new agents can be added without touching existing agents — they just read what they need
- **Negative**: adding a new field requires updating the TypedDict and every place that might need a zero value — coordinated change but low risk
- **Negative**: no runtime validation; if a LLM returns wrong types into a field, the error surfaces downstream

## Related

- [ADR-001](ADR-001-langgraph-orchestration.md) — LangGraph requires TypedDict or compatible
- [ADR-004](ADR-004-sqlite-audit.md) — audit persistence reads from PipelineState
