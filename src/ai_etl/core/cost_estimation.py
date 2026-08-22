"""Pre-run token/cost heuristic (Sprint 35, FinOps — pre-execution cost estimate).

Why an estimate is needed at all: real cost is only known after a run
computes actual `TokenUsage` and prices it via `core.pricing.compute_cost_usd`
(Sprint 3, ADR-008) — that happens mid-run, inside `agents/pipeline/orchestrator.py`
and `agents/pipeline/transformer.py`. Before a run starts, all that is known is
the dataset's shape (row/column count), so this module estimates the token
usage those two LLM calls would consume for a dataset of a given shape,
without actually running extraction.

Heuristic basis — verified against `agents/pipeline/extractor.py::_extract_schema`,
the schema summary threaded into both prompts (`ORCHESTRATOR_PROMPT`'s plan
request and `TRANSFORMER_PROMPT`'s `{schema_summary}`):
- `columns`/`dtypes`/`null_counts`/`null_ratio` are full-width — one entry per
  column, however many columns the dataset has. Token cost is dominated by
  `col_count`, not `row_count`.
- `sample` is `df.head(3)`, capped to `MAX_SAMPLE_COLUMNS` columns — a fixed,
  tiny slice of the data regardless of how many rows the dataset actually
  has. Row count's effect on prompt size is secondary at best;
  `TOKENS_PER_ROW` below captures a small residual (e.g. the `shape` value
  itself, longer sandbox error/retry text on very large datasets) rather
  than pretending row count is a first-order cost driver.

This is a first-pass calibration, not a regression fit against real runs —
Sprint 35's own "definição de pronto" calls for comparing estimates against
real executions' measured cost *after* this ships, not before. Revisit the
constants below once that comparison data exists.
"""

from ai_etl.core.analysis_types import TokenUsage

# Fixed overhead: the instructional text in ORCHESTRATOR_PROMPT/TRANSFORMER_PROMPT
# (rules, output-format instructions) that every run pays regardless of dataset
# shape. Rough order-of-magnitude estimate, not a token count of the literal
# prompt strings (which also vary by number of sources/transformations).
BASE_PROMPT_TOKENS = 700

# Per-column cost: one dtype entry, one null_count entry, one null_ratio entry,
# plus a share of the (capped) sample — roughly 25-45 tokens/column observed
# informally across the case-study scenarios' schema_summary payloads.
TOKENS_PER_COLUMN = 35.0

# Per-row cost: deliberately tiny — see module docstring for why row count
# barely moves prompt size (the sample is capped at 3 rows regardless of
# dataset size). Kept small enough that even a 1M-row dataset (a scale the
# capped 3-row sample makes irrelevant to prompt size) stays a minor
# contributor next to a realistic column-count difference — see
# `test_estimate_token_usage_row_count_effect_is_small_relative_to_columns`.
TOKENS_PER_ROW = 0.0001

# Transformer's generated code + Orchestrator's JSON plan are the "output"
# side of each call; a fixed ratio of the estimated total stands in for
# splitting input vs. output without knowing either prompt's exact
# tokenization in advance.
ESTIMATED_OUTPUT_TOKEN_RATIO = 0.35


def estimate_token_usage(row_count: int, col_count: int) -> TokenUsage:
    """Heuristic `TokenUsage` for a dataset with `row_count` rows and
    `col_count` columns, fed through Orchestrator + Transformer once each
    (the common case — no repair-loop retries, no multi-source fan-out
    accounted for). Raises `ValueError` for a negative row/column count."""
    if row_count < 0 or col_count < 0:
        raise ValueError("row_count and col_count must be non-negative")

    total_tokens = BASE_PROMPT_TOKENS + TOKENS_PER_COLUMN * col_count + TOKENS_PER_ROW * row_count
    output_tokens = round(total_tokens * ESTIMATED_OUTPUT_TOKEN_RATIO)
    input_tokens = round(total_tokens) - output_tokens
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
