"""Tests for `core/progress_messages.py` (2026-09-08 i18n fix)."""

from __future__ import annotations

import pytest

from ai_etl.core.progress_messages import agent_step, progress_message


def test_progress_message_pt_br() -> None:
    assert progress_message("pt-BR", "silver_start") == "⚡ Executando pipeline Silver..."


def test_progress_message_en_us() -> None:
    assert progress_message("en-US", "silver_start") == "⚡ Running Silver pipeline..."


def test_progress_message_formats_placeholders() -> None:
    msg = progress_message("en-US", "silver_done", total_time=4.2)
    assert msg == "✅ Silver completed in 4.2s"


def test_progress_message_unknown_locale_falls_back_to_default() -> None:
    """Same soft-fail convention as `core.locale.resolve_locale` — an unrecognized
    locale code (e.g. a stale/malformed DB value) never raises, falls back to
    `DEFAULT_LOCALE` (pt-BR)."""
    assert progress_message("fr-FR", "silver_start") == progress_message("pt-BR", "silver_start")


def test_progress_message_unknown_key_raises() -> None:
    """Unlike an unknown locale, an unknown message key is a programming error
    (a typo'd key at a call site) — it must not silently swallow into an empty
    or wrong string."""
    with pytest.raises(KeyError):
        progress_message("en-US", "no_such_key")


@pytest.mark.parametrize("locale", ["pt-BR", "en-US"])
def test_agent_step_known_node(locale: str) -> None:
    emoji, label, desc = agent_step(locale, "transformer")
    assert emoji == "⚙️"
    assert label == "Transformer"
    assert desc  # non-empty, locale-specific — exact wording checked below
    assert desc != agent_step("en-US" if locale == "pt-BR" else "pt-BR", "transformer")[2]


def test_agent_step_unknown_node_falls_back_generically() -> None:
    emoji, label, desc = agent_step("en-US", "some_future_node")
    assert emoji == "⚡"
    assert label == "some_future_node"
    assert desc == "Processing..."


def test_agent_step_label_is_locale_independent() -> None:
    """Proper nouns ('Orchestrator', 'Extractor', ...) stay identical across
    locales — only the description is translated."""
    _, label_pt, _ = agent_step("pt-BR", "orchestrator")
    _, label_en, _ = agent_step("en-US", "orchestrator")
    assert label_pt == label_en == "Orchestrator"
