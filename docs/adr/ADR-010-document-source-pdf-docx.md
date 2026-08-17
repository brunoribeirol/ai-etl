# ADR-010 — PDF/DOCX as a `sources/` connector, LLM structuring inline

**Status:** Accepted
**Date:** 2026-08-16
**Deciders:** Bruno Ribeiro

---

## Context

Sprint 5's scope (Vault: `artefact/sprint-roadmap.md`) includes heterogeneous-source coverage for OE4's evaluation — the case study needs a 4th scenario (CSV+Postgres+REST+PDF/DOCX) alongside the three already covered. Today `sources/` has three connectors (`csv_source.py`, `postgres_source.py`, `rest_source.py`), all following the same shape: one module-level `load_<type>(...) -> pd.DataFrame` function, no class/protocol, no source-level `try/except` (errors propagate; `extractor_node` in `agents/extractor.py` is the single catch point). None of them call an LLM — they are pure I/O.

Two questions needed resolving before implementation:

1. **Does PDF/DOCX need a 6th graph agent, or does it fit the existing 5-Silver/4-Agentic-BI agent count already documented in the TCC's Fundamentação Teórica?** Already decided in the roadmap doc (14/08/2026): **no new agent** — PDF/DOCX enters as a connector in `sources/`, matching the `csv_source.py`/`postgres_source.py` pattern, avoiding an undocumented architecture change mid-project.
2. **Where does the LLM call that structures raw extracted text into tabular rows live?** Unlike the other three connectors, a PDF/DOCX file's text isn't already tabular — turning "a paragraph of prose" or "a loosely-formatted table" into rows requires the same kind of LLM-assisted structuring `agents/orchestrator.py` already does for the NL spec → JSON pipeline plan, and `agents/transformer.py` does for code generation: build a prompt, `llm.invoke(...)`, parse the response, retry up to N times on a malformed response, surface a plain exception/error string on exhaustion.

## Decision

`sources/document_source.py` exposes `load_document(path: str) -> pd.DataFrame`, matching the existing connector signature shape (`load_csv`, `load_postgres`, `load_rest`). Internally:

1. **Text extraction** — `pypdf` for `.pdf`, `python-docx` for `.docx` (new dependencies; no PDF/DOCX library existed in `pyproject.toml` before this ADR). Unsupported extensions raise `ValueError`, consistent with `rest_source.py`'s unexpected-shape handling.
2. **LLM structuring** — the extracted text is sent to `core.llm.get_llm()` with a prompt asking for a JSON array of row objects, following `orchestrator_node`'s exact retry skeleton: up to 3 attempts, feed the previous invalid response back into the prompt on failure, raise a plain `ValueError` with the last error on exhaustion (not a `state["error"]`/`status="failed"` dict — that shape belongs to agent *nodes*, not `sources/` connectors, which raise and let `extractor_node` catch).

**The LLM call lives inside the connector itself**, not in a new agent or a separate `core/` helper. This was the explicit call already made in the roadmap doc ("faz uma chamada LLM pra estruturar o texto extraído — igual o Transformer já faz geração de código via LLM") — `document_source.py` is simply the first `sources/` connector to import `core.llm.get_llm`, the same dependency `agents/orchestrator.py` and `agents/transformer.py` already have. Keeping it in the connector (rather than splitting "extract raw text" and "structure via LLM" across two layers) matches this codebase's existing "connector does the work" framing and avoids inventing a new intermediate abstraction for a single new source type.

**Wiring**: `agents/extractor.py::extractor_node`'s `if/elif` dispatch gains an `elif source_type == "document": df = load_document(source["path"])` branch; `agents/orchestrator.py::ORCHESTRATOR_PROMPT`'s source-type list/schema gains `"document"` with its `path` key, so the Orchestrator LLM can route a spec like "load this PDF" into a `document`-typed source entry — no change to the dispatch mechanism itself (still LLM-inferred routing, not explicit code branching upstream of the extractor).

## Consequences

- **Positive**: closes OE4's 4th case-study scenario without inflating the documented agent count — `document_source.py` slots into the exact same connector pattern `csv_source.py`/`postgres_source.py`/`rest_source.py` already established, reviewable the same way.
- **Positive**: reuses `core.llm.get_llm()` and the retry-loop shape already proven in `orchestrator_node`/`transformer_node` — no new LLM-calling convention introduced.
- **Negative**: `sources/` stops being purely-I/O-with-zero-LLM-calls as a blanket property; a future reader of `sources/__init__.py`-level docs should know `document_source.py` is the exception. Worth a one-line note in the module docstring (present in the implementation).
- **Negative**: new dependencies (`pypdf`, `python-docx`) and a real LLM cost per document processed (unlike the other three connectors, which are free/local-network I/O) — acceptable for a case-study scenario, but a production SaaS tenant using this connector heavily would see it show up in `core/pricing.py`'s per-run cost the same way Analyst/Science sub-tasks already do (this connector's LLM call is not currently token-metered into `analysis_runs` the way agent nodes are — out of scope for this ADR, flagged for a future metering pass if `document` sources see real usage).
- **Neutral**: no database migration, no new agent, no change to `PipelineState`'s shape beyond the existing `pipeline_plan.sources[].type` enum gaining one more valid value.

## Related

- Vault: `artefact/sprint-roadmap.md` — Sprint 5 scope, and the "Agentes de produto" section's prior no-new-agent decision this ADR formalizes.
- `src/ai_etl/sources/csv_source.py`, `postgres_source.py`, `rest_source.py` — the connector pattern this ADR follows.
- `src/ai_etl/agents/orchestrator.py`, `agents/transformer.py` — the LLM retry-loop pattern this ADR reuses.
- `src/ai_etl/agents/extractor.py` — the dispatch point `document` is wired into.
