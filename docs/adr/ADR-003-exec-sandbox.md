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

Use **restricted `exec()`** in `src/ai_etl/core/sandbox.py`.

The sandbox:
- Passes a whitelist of globals: `{"__builtins__": safe_builtins, "pd": pandas, "np": numpy}`
- `safe_builtins` excludes: `open`, `__import__`, `eval`, `exec`, `compile`, `getattr`, `setattr`, `vars`, `globals`, `locals`
- The generated function must be named `transform(df)` and return a `pd.DataFrame`
- Any exception from the sandboxed code is caught and returned as an error string

**Known limitation (documented in `SECURITY.md`):** Python's `exec()` sandbox can be escaped via C extensions or `ctypes`. For the TCC context with controlled datasets, this is acceptable. A production deployment would require Docker or a gVisor sandbox.

Bandit's B102 (`exec()` usage) is suppressed with `# nosec B102` only in `sandbox.py`, never elsewhere.

## Consequences

- **Positive**: full automation — no human in the loop for code execution
- **Positive**: prevents the most common LLM code risks (file access, HTTP calls, env var leakage)
- **Positive**: fast — no subprocess or network overhead
- **Negative**: not production-safe against adversarial LLM outputs; documented and scoped to TCC context
- **Negative**: only `pandas` and `numpy` available — LLM cannot use other libraries in transformation code

## Related

- [ADR-001](ADR-001-langgraph-orchestration.md) — Transformer node in the LangGraph pipeline
- `SECURITY.md` — full risk analysis and mitigations
