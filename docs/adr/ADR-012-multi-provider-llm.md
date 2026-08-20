# ADR-012: Multi-provider LLM support (OpenAI, Anthropic, Google, Ollama)

**Status:** Proposed
**Date:** 2026-08-19
**Sprint:** 23 (product roadmap), pulled forward at owner's explicit request — see
`~/Documents/Obsidian Vault/tcc/artefact/product-roadmap-post-tcc.md`.

## Context

`core/llm.py` has always instantiated `ChatOpenAI` directly. Every LLM-calling agent
(Orchestrator, Transformer, Planner, Analyst, Science, Advisor — 6 real call sites,
confirmed via `grep -rn "get_llm(" src/ai_etl/`) plus `sources/document_source.py`'s
structuring step (7 total; `get_model_name()` is also read by `audit/db.py` for
cost-tracking without instantiating a client) depends on `get_llm()` returning a
LangChain `BaseChatModel`. None of these call sites should need to know which provider
is behind it.

The owner wants real support for three more providers — Anthropic (Claude), Google
(Gemini), and local models via Ollama — selectable via configuration, not hardcoded.
This is scoped explicitly to *provider selection*, not automatic fallback: fallback
between providers (health checks, automatic retry-on-different-provider in production)
is a larger decision deferred to the full Sprint 23 scope in the roadmap and is
**out of scope for this ADR**.

## Decision

### 1. Provider abstraction: a factory function, not a class hierarchy

`get_llm()` keeps its existing signature (`temperature: float = 0.0) -> BaseChatModel`)
and return type. Internally it dispatches on a new `AI_ETL_LLM_PROVIDER` env var
(`openai` | `anthropic` | `google` | `ollama`, default `openai`) to one of four small
private builder functions (`_build_openai`, `_build_anthropic`, `_build_google`,
`_build_ollama`), each returning the provider's LangChain `ChatModel` class
(`ChatOpenAI`, `ChatAnthropic`, `ChatGoogleGenerativeAI`, `ChatOllama`). All four
already implement `BaseChatModel` (`langchain_core.language_models.chat_models`), so
this is a pure Strategy/factory — no new abstract base class or Protocol is needed
on top of what LangChain already provides. This keeps the change minimal: none of the
7 call sites change, because they already only depend on `BaseChatModel`'s interface
(`.invoke()`, `.usage_metadata` on responses via `extract_token_usage()`).

A `Protocol`-based abstraction was considered and rejected: it would duplicate
`BaseChatModel`'s surface for no behavioral gain, since every candidate provider's
LangChain integration already satisfies it.

### 2. Env vars and model-name mapping

```
AI_ETL_LLM_PROVIDER=openai|anthropic|google|ollama   # default: openai
AI_ETL_LLM_MODEL=<provider-specific model id>          # meaning depends on provider
ANTHROPIC_API_KEY=...        # required iff provider=anthropic
GOOGLE_API_KEY=...           # required iff provider=google
OLLAMA_BASE_URL=http://localhost:11434   # optional, provider=ollama only
```

`AI_ETL_LLM_MODEL` keeps its name across providers (no `AI_ETL_ANTHROPIC_MODEL`, etc.)
because only one provider is ever active per deployment — a second model-name var per
provider would be dead configuration 3/4 of the time. Each provider has its own
default model name, applied when `AI_ETL_LLM_MODEL` is unset, so switching
`AI_ETL_LLM_PROVIDER` alone (with no model override) still produces a sane pairing:

| Provider | Default `AI_ETL_LLM_MODEL` |
|---|---|
| `openai` | `gpt-4o-mini` (unchanged) |
| `anthropic` | `claude-sonnet-5` |
| `google` | `gemini-2.0-flash` |
| `ollama` | `llama3.1` |

No cross-provider name translation is attempted (e.g. mapping `gpt-4o-mini` to a
Claude equivalent) — `AI_ETL_LLM_MODEL` is always a literal, provider-native model id.
Silent translation would be a worse failure mode than requiring the operator to set
the right value for the provider they chose.

### 3. Fail-fast on missing credentials

Each `_build_*` function reads its own required credential directly and raises
`RuntimeError` with a clear, provider-specific message
(`"AI_ETL_LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set"`) when it is
missing, before constructing the client. This is deliberate: the underlying LangChain
client classes already raise on missing credentials, but their error messages are
generic/library-internal and don't say *which env var* the operator needs to set for
*this* codebase's config surface. An unknown/misspelled `AI_ETL_LLM_PROVIDER` value
also raises `RuntimeError` immediately, never silently falling back to OpenAI — silent
fallback would hide a config typo in production until the first LLM call, at which
point the failure is much harder to trace back to its cause.

`ollama` has no required credential (`OLLAMA_BASE_URL` defaults to
`http://localhost:11434`, matching Ollama's own default) — fail-fast doesn't apply
there beyond the unreachable-server error `ChatOllama` itself raises on first call.

### 4. Explicitly out of scope

- **Automatic fallback between providers** (e.g. retry on Anthropic if OpenAI is down)
  — full Sprint 23 scope in the roadmap, not this round.
- **Health checks / readiness probes** per provider.
- **Cost tracking per provider** (`core/pricing.py`'s `compute_cost_usd()` is
  OpenAI-model-priced only today; extending it to Anthropic/Google/Ollama pricing
  tables is a separate, follow-up piece of work — flagged here, not solved).
- **Per-agent provider override** (e.g. Transformer on Claude, Advisor on GPT) — today
  `AI_ETL_LLM_PROVIDER` is a single global setting for the whole pipeline, matching
  the existing single-`AI_ETL_LLM_MODEL` behavior.

### 5. Dependency placement: base dependencies, not extras

`langchain-anthropic`, `langchain-google-genai`, and `langchain-ollama` are added to
`pyproject.toml`'s base `dependencies`, not to `[project.optional-dependencies]`.
Reasoning: `get_llm()` is called unconditionally by every agent regardless of which
provider is configured at runtime — an extras-based install would require every
deployment (including the default OpenAI-only ones already running in production) to
know in advance which extras to install, or `core/llm.py` would need a deferred/lazy
import per provider with its own try/except-ImportError-and-explain-which-extra
machinery. That complexity buys nothing here: none of these three packages pull in
heavy runtime dependencies (unlike, say, `torch`), so the install-size cost of
including all four providers unconditionally is small, and it keeps `get_llm()`'s
import section simple and fail-fast at process startup (an import error surfaces
immediately, not on the first pipeline run).

## Consequences

- All 7 existing `get_llm()`/`get_model_name()` call sites are unchanged — verified by
  re-running `grep -rn "get_llm(" src/ai_etl/"` post-change and confirming identical
  call sites/signatures.
- `.env.example` documents the new vars, all commented/opt-in except the existing
  `OPENAI_API_KEY`/`AI_ETL_LLM_MODEL` pair, preserving default-OpenAI behavior with a
  completely unmodified `.env`.
- `make security` (bandit + pip-audit) must be re-run after adding the three new
  dependencies — see the PR description for the actual run's output.
- Real credentials for Anthropic/Google/Ollama are not available in this environment;
  provider selection and instantiation are verified with mocked env vars (asserting
  the correct LangChain class is constructed with the correct kwargs), not live API
  calls. This is documented plainly in the PR rather than claimed as end-to-end
  verification — a follow-up with real credentials (or a `RUN_LIVE_LLM_TESTS`-gated
  integration test) is left for whoever configures those credentials in CI/production.

## Alternatives considered

- **LiteLLM / a unified third-party LLM router** — rejected: adds a new dependency and
  abstraction layer on top of LangChain's own multi-provider `BaseChatModel` support,
  which already does exactly what's needed here with zero extra abstraction.
- **A `Protocol`/ABC named `LLMProvider` wrapping each client** — rejected, see
  Decision §1: it would duplicate `BaseChatModel` for no behavioral gain.
