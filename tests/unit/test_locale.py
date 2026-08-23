"""Unit tests for core/locale.py (Sprint 25, ADR-036)."""

from ai_etl.core.locale import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    currency_hint,
    date_parse_hint,
    get_locale_metadata,
    narrative_language_instruction,
    resolve_locale,
)


def test_default_locale_is_pt_br() -> None:
    assert DEFAULT_LOCALE == "pt-BR"


def test_supported_locales_contains_exactly_pt_br_and_en_us() -> None:
    assert set(SUPPORTED_LOCALES) == {"pt-BR", "en-US"}


def test_resolve_locale_passes_through_a_supported_locale() -> None:
    assert resolve_locale("en-US") == "en-US"
    assert resolve_locale("pt-BR") == "pt-BR"


def test_resolve_locale_falls_back_to_default_for_none() -> None:
    assert resolve_locale(None) == DEFAULT_LOCALE


def test_resolve_locale_falls_back_to_default_for_unknown_input() -> None:
    assert resolve_locale("fr-FR") == DEFAULT_LOCALE
    assert resolve_locale("") == DEFAULT_LOCALE
    assert resolve_locale("garbage") == DEFAULT_LOCALE


def test_get_locale_metadata_returns_expected_fields_for_pt_br() -> None:
    meta = get_locale_metadata("pt-BR")
    assert meta["dayfirst"] is True
    assert meta["currency_symbol"] == "R$"
    assert meta["currency_code"] == "BRL"


def test_get_locale_metadata_returns_expected_fields_for_en_us() -> None:
    meta = get_locale_metadata("en-US")
    assert meta["dayfirst"] is False
    assert meta["currency_symbol"] == "$"
    assert meta["currency_code"] == "USD"


def test_get_locale_metadata_falls_back_to_default_for_unknown_locale() -> None:
    assert get_locale_metadata("fr-FR") == get_locale_metadata(DEFAULT_LOCALE)


def test_narrative_language_instruction_mentions_the_language_and_code() -> None:
    instruction = narrative_language_instruction("en-US")
    assert "English (US)" in instruction
    assert "en-US" in instruction


def test_date_parse_hint_prefers_dayfirst_for_pt_br() -> None:
    hint = date_parse_hint("pt-BR")
    assert "dayfirst=True FIRST" in hint


def test_date_parse_hint_prefers_month_first_for_en_us() -> None:
    hint = date_parse_hint("en-US")
    assert "month-first" in hint
    assert "FIRST" in hint


def test_currency_hint_mentions_the_right_symbol_per_locale() -> None:
    assert "R$" in currency_hint("pt-BR")
    assert "$" in currency_hint("en-US")
