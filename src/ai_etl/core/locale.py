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
    """Transformer prompt block: which `pd.to_datetime` reading to try first for this
    tenant's locale, mirroring (and inverting, when day-first) the existing
    NaT-count tie-break fallback described in `agents/pipeline/transformer.py`."""
    meta = get_locale_metadata(locale)
    if meta["dayfirst"]:
        return (
            f"This tenant's locale is {locale} ({meta['date_format_hint']}, day-first). When "
            "parsing a date/datetime column with `pd.to_datetime`, try dayfirst=True FIRST. "
            "Only fall back to the default (month-first) parse if it produces fewer NaT values "
            "than the dayfirst=True attempt — keep whichever attempt produced fewer NaT, same "
            "tie-break rule as before, just with the two attempts' priority order swapped for "
            "this tenant."
        )
    return (
        f"This tenant's locale is {locale} ({meta['date_format_hint']}, month-first — pandas' "
        "own default). When parsing a date/datetime column with `pd.to_datetime`, try the "
        "default (month-first) parse FIRST, falling back to `dayfirst=True` only if it produces "
        "more than 5% NaT — keep whichever attempt produced fewer NaT."
    )


def currency_hint(locale: str) -> str:
    """One-line formatting guidance for any monetary value the LLM narrates."""
    meta = get_locale_metadata(locale)
    return f"Format any monetary value as {meta['currency_symbol']} ({meta['currency_code']})."
