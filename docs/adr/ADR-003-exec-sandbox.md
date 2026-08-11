# ADR-003 — Execute LLM-generated code inside a restricted exec() sandbox

**Status:** Accepted (with documented limitations)  
**Date:** 2026-06-22  
**Deciders:** Bruno Ribeiro

---

## Context

The Transformer agent generates Python code via an LLM and must execute it on the extracted DataFrame. Options:

1. **No execution**: return generated code only, require human execution — defeats the purpose of full automation
2. **Unrestricted exec()**: simple but allows LLM-generated code to read files, make network calls, or access environment variables
3. **Restricted exec() with custom globals**: blocks dangerous builtins, allows only `pandas` and `numpy`
4. **Docker subprocess**: strongest isolation but requires Docker in the runtime environment and adds significant latency

## Decision

Use **restricted `exec()`**, called from three separate sites, each with its own globals whitelist:

| Site | Agent | Globals available |
|---|---|---|
| `src/ai_etl/core/sandbox.py` | Transformer (Silver pipeline) | `pandas`, `numpy`; `SAFE_BUILTINS` includes `getattr`/`hasattr`/`isinstance`/`issubclass`/`type` (no `setattr`, `vars`, `open`, `__import__`, `eval`, `exec`, `compile`) |
| `src/ai_etl/agents/analyst.py` (`_SAFE_GLOBALS`) | Analyst / Gold agent | `pandas`, `numpy`, `plotly.express`, `plotly.graph_objects`; builtins additionally include `setattr`, `vars`, `iter`, `next`, `repr`, `format` |
| `src/ai_etl/agents/science.py` (`_SAFE_GLOBALS`) | Science agent | same as `analyst.py`, plus `slice`, plus pre-imported `sklearn` (`LinearRegression`, `Ridge`, `RandomForestRegressor`, `RandomForestClassifier`, `KMeans`, model-selection/metrics/preprocessing helpers) and `statsmodels` (`ExponentialSmoothing`, `ARIMA`) classes injected directly as globals |

This ADR originally described only the first site, written when the Transformer was the only agent executing generated code. The Analyst and Science agents (added for the Streamlit "Agentic BI" extension, commit `8b37d23`) each grew their own, more permissive, copy of the same pattern instead of reusing `core/sandbox.py` — they need extra builtins (`setattr`, `vars`) and injected libraries (Plotly, sklearn, statsmodels) that the Transformer's minimal DataFrame-only contract doesn't require. This is tracked as unintended drift, not a deliberate per-agent design choice — see "Follow-up" below.

The generated function contract differs per site: `transform(df) -> pd.DataFrame` (Transformer), a script producing `gold_df`/`fig`/`narrative` (Analyst), a script producing `predictions_df`/`fig`/`narrative`/`model_info` (Science). Any exception from sandboxed code is caught and returned as an error string, feeding the agent's retry loop.

**Known limitation (all three sites):** Python's `exec()` sandbox can be escaped via C extensions, `ctypes`, or object-graph introspection (`().__class__.__mro__[1].__subclasses__()`) — the broader builtin sets in `analyst.py`/`science.py` (`getattr`, `setattr`, `vars`) make this *easier*, not harder, since the escape technique only needs attribute introspection. For the TCC context with controlled datasets, this is accepted for all three sites. A production deployment would require Docker or a gVisor sandbox, and none of the three sites currently apply the `timeout_seconds` value some callers accept — no execution-time limit is actually enforced anywhere.

Bandit's B102 (`exec()` usage) is suppressed with `# nosec` at each of the three `exec()` call sites (`sandbox.py`, `analyst.py`, `science.py`) — not only in `sandbox.py` as originally stated here.

## Follow-up (open as of 2026-08)

The three independent globals whitelists are a maintenance and audit risk: a fix applied to one (e.g. narrowing builtins) silently does not apply to the others. Before this is presented as a settled design, either (a) unify all three call sites behind `core/sandbox.py` with a per-agent allowlist of *extra* symbols layered on top of one shared base, or (b) explicitly document why Analyst/Science need a materially different security boundary than Transformer. Not yet decided; flagged here so the gap is visible rather than implied to not exist.

## Consequences

- **Positive**: full automation — no human in the loop for code execution
- **Positive**: prevents the most common LLM code risks (file access, HTTP calls, env var leakage) at all three sites
- **Positive**: fast — no subprocess or network overhead
- **Negative**: not production-safe against adversarial LLM outputs at any of the three sites; documented and scoped to TCC context
- **Negative**: three independent implementations of the same pattern, with diverging permissiveness, instead of one shared, reviewed boundary
- **Negative**: declared `timeout_seconds` parameters are not enforced — a sandboxed call can run indefinitely
- **Negative**: Transformer is limited to `pandas`/`numpy`; Analyst additionally has Plotly; Science additionally has sklearn/statsmodels — library availability is per-agent, not a single documented contract

## Related

- [ADR-001](ADR-001-langgraph-orchestration.md) — Transformer node in the LangGraph pipeline
- `SECURITY.md` — full risk analysis and mitigations
- `src/ai_etl/agents/analyst.py`, `src/ai_etl/agents/science.py` — the two additional `exec()` call sites covered by this ADR since the Agentic BI extension
