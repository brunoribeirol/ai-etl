# Skill: add-agent

Add a new LangGraph agent node to the pipeline.

## Steps

### 1. Create the agent file

Create `src/ai_etl/agents/<name>.py` following this exact contract:

```python
"""<Name> Agent — <one-line description>."""

from typing import Any
from ai_etl.audit.logger import log_action
from ai_etl.core.state import PipelineState


def <name>_node(state: PipelineState) -> PipelineState:
    # Always short-circuit on upstream errors
    if state.get("error"):
        return state

    # ... agent logic here ...

    new_log = log_action(state, "<name>", "<action>", {"key": "value"})
    return {**state, "field": result, "audit_log": new_log}
```

Non-negotiable rules:
- Signature: `(state: PipelineState) -> PipelineState`
- Return: `{**state, ...}` — never mutate state in-place
- Always call `log_action()` for each significant action
- Always short-circuit if `state.get("error")` is truthy

### 2. Register in the graph

In `src/ai_etl/core/graph.py`:
```python
from ai_etl.agents.<name> import <name>_node

graph.add_node("<name>", <name>_node)
graph.add_edge("<previous_node>", "<name>")
graph.add_edge("<name>", "<next_node>")
```

### 3. Add to PipelineState if needed

If the agent writes new fields, add them to `PipelineState` in `src/ai_etl/core/state.py` and initialize them in `initial_state()`.

### 4. Write unit tests

Create `tests/unit/test_<name>.py` with at minimum:
- Happy path: agent transforms state correctly
- Short-circuit: upstream error returns state unchanged
- Audit log: `log_action` entry is added
- Error handling: agent sets `error` field on failure

### 5. Update docs

- Add the agent to the vault: `artefact/architecture.md` (graph section + agent spec)
- Update the pipeline diagram in `README.md` if the topology changed

### 6. Verify

```bash
make check  # lint + type-check + test + security
```

Coverage must stay above 80%.
