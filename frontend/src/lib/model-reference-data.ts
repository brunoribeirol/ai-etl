/**
 * Static reference data for the model-selection UI (`components/model-picker.tsx`).
 *
 * Only the 4 cloud models a tenant can actually pick via
 * `PUT /pipelines/{id}/llm-config` (`core.llm.ALLOWED_MODELS_BY_PROVIDER`,
 * restricted here to `openai`/`anthropic`) — `google` isn't listed at all: it
 * exists in that backend allowlist but was never part of the 2026-08-23
 * comparison, so there's no real reliability data to show for it yet, omitted
 * rather than shown with a fabricated number.
 *
 * Product decision (2026-08-23): local (Ollama) models are never shown here at
 * all, not even as a non-selectable reference — they will never be
 * client-selectable (no way to run genuinely local inference from a shared
 * Railway-hosted deployment), and the owner's own machine got measurably
 * slower running one just for this comparison, reinforcing that it has no
 * place in the product UI even as a static reference card.
 *
 * Two things intentionally do NOT come from a live API call:
 *   - `inputPricePerMillionUsd`/`outputPricePerMillionUsd` mirror
 *     `core/pricing.py::MODEL_PRICING_USD_PER_MILLION_TOKENS` exactly — update
 *     both together if a price ever changes there.
 *   - `reliability`/`reliabilityNote` come from the real, credentialed 6-model
 *     comparison run 2026-08-23 (`case_study/results/model_comparison_2026-08-23*`,
 *     `docs/CURRENT_STATE.md`) — a real measurement, not a live metric this
 *     deployment recomputes per request (no endpoint for that exists yet).
 *     Update this file by hand the same way, whenever the comparison is re-run.
 */
export type ModelReferenceEntry = {
  provider: "openai" | "anthropic";
  model: string;
  displayName: string;
  inputPricePerMillionUsd: number;
  outputPricePerMillionUsd: number;
  reliability: "verified";
  reliabilityNote: string;
};

export const MODEL_REFERENCE_DATA: ModelReferenceEntry[] = [
  {
    provider: "openai",
    model: "gpt-4o-mini",
    displayName: "GPT-4o mini",
    inputPricePerMillionUsd: 0.15,
    outputPricePerMillionUsd: 0.6,
    reliability: "verified",
    reliabilityNote: "3/3 real runs completed, quality score 88.0, ~$0.0006/run.",
  },
  {
    provider: "openai",
    model: "gpt-4o",
    displayName: "GPT-4o",
    inputPricePerMillionUsd: 2.5,
    outputPricePerMillionUsd: 10.0,
    reliability: "verified",
    reliabilityNote:
      "3/3 real runs completed, quality score 88.0, ~$0.02-0.09/run (more retry-prone than gpt-4o-mini in practice).",
  },
  {
    provider: "anthropic",
    model: "claude-haiku-4-5",
    displayName: "Claude Haiku 4.5",
    inputPricePerMillionUsd: 1.0,
    outputPricePerMillionUsd: 5.0,
    reliability: "verified",
    reliabilityNote:
      "3/3 real runs completed after the markdown-fence fix (PR #109), quality score 88.0, ~$0.017/run — confirms the fix resolved what was previously a 100% failure rate for this model.",
  },
  {
    provider: "anthropic",
    model: "claude-sonnet-5",
    displayName: "Claude Sonnet 5",
    inputPricePerMillionUsd: 3.0,
    outputPricePerMillionUsd: 15.0,
    reliability: "verified",
    reliabilityNote: "3/3 real runs completed after a temperature-parameter fix, quality score 83.0-88.0, ~$0.08-0.10/run.",
  },
];
