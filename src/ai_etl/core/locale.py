"""Locale metadata and resolution (Sprint 25, ADR-036).

Single source of truth for the tenant-configured locale — date/currency formatting hints
and the narrative-language instruction threaded into `agents/pipeline/transformer.py`'s
prompt and every `agents/analysis/*.py` prompt (mirroring how `core/llm.py`'s
`ALLOWED_MODELS_BY_PROVIDER` is the single source of truth for provider/model validation,
ADR-031 §2).
"""

from __future__ import annotations

from typing import Optional

from typing_extensions import TypedDict


class LocaleMetadata(TypedDict):
    language_name: str  # human-readable, used in the LLM prompt instruction
    dayfirst: bool  # True = DD/MM/YYYY convention (most of the world), False = MM/DD/YYYY (US)
    date_format_hint: str  # shown to the LLM as a formatting example
    currency_symbol: str
    currency_code: str
    timezone: str


DEFAULT_LOCALE = "pt-BR"

LOCALE_METADATA: dict[str, LocaleMetadata] = {
    "pt-BR": {
        "language_name": "Portuguese (Brazil)",
        "dayfirst": True,
        "date_format_hint": "DD/MM/YYYY",
        "currency_symbol": "R$",
        "currency_code": "BRL",
        "timezone": "America/Sao_Paulo",
    },
    "en-US": {
        "language_name": "English (US)",
        "dayfirst": False,
        "date_format_hint": "MM/DD/YYYY",
        "currency_symbol": "$",
        "currency_code": "USD",
        "timezone": "America/New_York",
    },
}

# Only these two are valid at launch (ADR-036 §1) — a fixed, reviewed allowlist, same
# "validated once at the API boundary" pattern as `core.llm.ALLOWED_MODELS_BY_PROVIDER`.
SUPPORTED_LOCALES: tuple[str, ...] = tuple(LOCALE_METADATA.keys())


def resolve_locale(raw: Optional[str]) -> str:
    """Coerce any input into a supported locale code, defaulting to `DEFAULT_LOCALE` for
    `None`/unknown/malformed input — soft-fail, never raises. Mirrors this codebase's
    existing "coerce, don't reject" convention for stored config values (e.g.
    `_saved_pipeline_row_to_dict`'s handling of legacy rows) rather than
    `core.llm.validate_provider_and_model`'s raise-on-invalid, since this function is
    used to interpret already-read-back DB values, not to gate a write."""
    if raw in LOCALE_METADATA:
        return raw
    return DEFAULT_LOCALE


def get_locale_metadata(locale: str) -> LocaleMetadata:
    """Metadata for a locale, falling back to `DEFAULT_LOCALE`'s if `locale` isn't
    recognized (defensive — callers should already have passed it through
    `resolve_locale`, but this avoids a `KeyError` if one doesn't)."""
    return LOCALE_METADATA.get(locale, LOCALE_METADATA[DEFAULT_LOCALE])


def narrative_language_instruction(locale: str) -> str:
    """The sentence dropped into every Analyst/Science/Advisor/Planner prompt in place
    of the old hardcoded "in Portuguese" instruction."""
    meta = get_locale_metadata(locale)
    return f"Write all narrative text in {meta['language_name']} ({locale})."


def date_parse_hint(locale: str) -> str:
    """Transformer prompt block: which `pd.to_datetime` reading to *prefer* for this
    tenant's locale, and ONLY for values that are not already unambiguous ISO.

    Real, live-verified bug found 2026-09-04, TWICE. First version of this hint
    compared `NaT` counts between the two readings — failed because an ISO date
    (`2026-01-02`) parses successfully under both `dayfirst=True` and the default
    (0 `NaT` either way), so the NaT tie-break never triggered and whichever was
    tried first silently won. Second version (same day) switched to comparing
    whether the two readings *disagree* on any row, reasoning that agreement meant
    "unambiguous, doesn't matter which." That reasoning was wrong, confirmed by
    re-testing live: `dayfirst=True` re-reads which token is day vs month, so it
    produces a genuinely DIFFERENT (and wrong) timestamp for ISO dates whenever day
    and month are both ≤ 12 (`2026-02-01` -> `2026-01-02`) — the two readings
    *disagree* precisely in the case they should have agreed, so the disagreement
    check picked the locale-preferred (wrong) reading exactly when it shouldn't
    have. Comparing parse *results* can never distinguish "unambiguous ISO" from
    "genuinely ambiguous day-first text" — both produce two different, plausible
    timestamps either way. The only reliable signal is the STRING FORMAT itself: a
    strict `format="%Y-%m-%d"` parse either matches every value or it doesn't.
    Locale preference below applies ONLY to values that fail that strict ISO
    check — see the prompt's own RIGHT example for the exact pattern.

    Wave 3 item 16, 2026-09-05 mega-audit remediation
    (docs/archive/work/2026-09-05-mega-audit-remediation-plan.md): live-verified
    real gap — the strict-ISO check above only helps a column that is
    *purely* ISO; a column genuinely MIXING ISO and non-ISO strings still
    fell to a whole-column `pd.to_datetime(...)` call for the non-ISO
    branch, and pandas infers ONE format from the column and applies it to
    every row, silently turning the minority-format rows into NaT even
    though each string is individually parseable. The prompt's RIGHT
    example now parses per-value (`.apply()`), which sidesteps that
    column-wide format inference entirely — this hint no longer talks about
    comparing NaT counts across the whole column (that comparison doesn't
    apply per-value: a single value either parses under the tenant's
    preferred reading or it doesn't).

    Third variant, found 2026-10-12 by the first real run of the public-data
    case study: the strict check was `format="%Y-%m-%d"`, which matches a date
    alone but rejects an ISO value with a time part ("2025-01-06 00:04:25").
    Those values fell to the locale branch and were re-read day-first: 19,974 of
    20,000 NYC TLC timestamps came back with day and month swapped while the run
    reported success. The prompt's check is now `format="ISO8601"`, and this hint
    says "with or without a time part" so the model does not narrow it again."""
    meta = get_locale_metadata(locale)
    if meta["dayfirst"]:
        return (
            f"This tenant's locale is {locale} ({meta['date_format_hint']}, day-first). Only "
            "for values that are NOT already unambiguous ISO (YYYY-MM-DD, with or without a "
            "time part) — see the strict-ISO check in the pattern above, applied per value — "
            "use the dayfirst=True reading for this tenant on each such value."
        )
    return (
        f"This tenant's locale is {locale} ({meta['date_format_hint']}, month-first — pandas' "
        "own default). Only for values that are NOT already unambiguous ISO (YYYY-MM-DD, with "
        "or without a time part) — see the strict-ISO check in the pattern above, applied per "
        "value — use the default (month-first) reading for this tenant on each such value."
    )


def currency_hint(locale: str) -> str:
    """One-line formatting guidance for any monetary value the LLM narrates."""
    meta = get_locale_metadata(locale)
    return f"Format any monetary value as {meta['currency_symbol']} ({meta['currency_code']})."
