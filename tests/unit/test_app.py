"""Tests for app.py — the Streamlit UI.

Two layers of coverage:
1. Pure helper functions, imported directly and unit-tested like any other module.
2. An `AppTest` smoke test that actually boots the app end-to-end in-process. This is
   the test that would have caught the `ModuleNotFoundError` and `TypeError` incidents
   found while manually driving the app with a browser this session — those were both
   failures at script-execution time, invisible to any test that only imports
   individual functions instead of running the file the way Streamlit does.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import app as app_module

# ---------------------------------------------------------------------------
# _auto_generate_spec
# ---------------------------------------------------------------------------


def test_auto_generate_spec_includes_file_path_and_columns() -> None:
    df = pd.DataFrame({"name": ["A"], "price": [1.0]})
    spec = app_module._auto_generate_spec(Path("runs/uploads/abc123.csv"), df, Path("out.csv"))

    assert "runs/uploads/abc123.csv" in spec
    assert "name, price" in spec
    assert "out.csv" in spec


def test_auto_generate_spec_includes_business_question_hint() -> None:
    df = pd.DataFrame({"a": [1]})
    spec = app_module._auto_generate_spec(
        Path("f.csv"), df, Path("out.csv"), business_question="Quais produtos vendem mais?"
    )

    assert "Quais produtos vendem mais?" in spec


def test_auto_generate_spec_forbids_fabricating_critical_fields() -> None:
    """Regression guard: this instruction is what stopped the Transformer from
    inventing a fake row (fillna("Unknown") etc.) for missing name/category/price."""
    df = pd.DataFrame({"name": ["A"]})
    spec = app_module._auto_generate_spec(Path("f.csv"), df, Path("out.csv"))

    assert "Do NOT fabricate values" in spec
    assert "is_incomplete" in spec


def test_auto_generate_spec_reports_row_and_column_counts() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    spec = app_module._auto_generate_spec(Path("f.csv"), df, Path("out.csv"))

    assert "3 rows and 2 columns" in spec


# ---------------------------------------------------------------------------
# _save_upload_to_temp
# ---------------------------------------------------------------------------


def test_save_upload_to_temp_writes_file_with_short_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)
    uploaded = MagicMock()
    uploaded.name = "sales.csv"
    uploaded.getvalue.return_value = b"a,b\n1,2\n"

    dest = app_module._save_upload_to_temp(uploaded)

    assert dest.exists()
    assert dest.read_bytes() == b"a,b\n1,2\n"
    assert dest.suffix == ".csv"
    # Short id (regression guard): a 36-char UUID risks LLM transcription typos when
    # the Orchestrator has to copy this path verbatim into the plan JSON.
    assert len(dest.stem) <= 12


def test_save_upload_to_temp_preserves_extension(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)
    uploaded = MagicMock()
    uploaded.name = "data.xlsx"
    uploaded.getvalue.return_value = b"binary"

    dest = app_module._save_upload_to_temp(uploaded)

    assert dest.suffix == ".xlsx"


# ---------------------------------------------------------------------------
# _make_progress_adapter — bridges pipeline_service.ProgressCallback to st.status
# ---------------------------------------------------------------------------


def test_progress_adapter_opens_one_status_box_per_stage(monkeypatch) -> None:
    created: list[str] = []

    class _FakeBox:
        def __init__(self) -> None:
            self.written: list[str] = []
            self.updated: list[tuple[str, str]] = []

        def write(self, message: str) -> None:
            self.written.append(message)

        def update(self, label: str, state: str) -> None:
            self.updated.append((label, state))

    def _fake_status(message: str, expanded: bool = True) -> _FakeBox:
        created.append(message)
        return _FakeBox()

    monkeypatch.setattr(app_module.st, "status", _fake_status)

    callback = app_module._make_progress_adapter()
    callback("silver", "⚡ Executando pipeline Silver...")
    callback("gold:0", "🏅 Gold — pergunta 1")
    callback("silver", "⚙️ **Transformer** — ... *(1.0s)*")

    assert created == ["⚡ Executando pipeline Silver...", "🏅 Gold — pergunta 1"]


def test_progress_adapter_finalizes_box_on_done_glyph(monkeypatch) -> None:
    class _FakeBox:
        def __init__(self) -> None:
            self.updated: list[tuple[str, str]] = []
            self.written: list[str] = []

        def write(self, message: str) -> None:
            self.written.append(message)

        def update(self, label: str, state: str) -> None:
            self.updated.append((label, state))

    boxes: dict[str, _FakeBox] = {}

    def _fake_status(message: str, expanded: bool = True) -> _FakeBox:
        box = _FakeBox()
        boxes["silver"] = box
        return box

    monkeypatch.setattr(app_module.st, "status", _fake_status)

    callback = app_module._make_progress_adapter()
    callback("silver", "⚡ Executando pipeline Silver...")
    callback("silver", "✅ Silver concluído em 3.0s")

    assert boxes["silver"].updated == [("✅ Silver concluído em 3.0s", "complete")]


def test_progress_adapter_treats_warning_glyph_as_error_state(monkeypatch) -> None:
    class _FakeBox:
        def __init__(self) -> None:
            self.updated: list[tuple[str, str]] = []

        def write(self, message: str) -> None:
            pass

        def update(self, label: str, state: str) -> None:
            self.updated.append((label, state))

    boxes: dict[str, _FakeBox] = {}

    def _fake_status(message: str, expanded: bool = True) -> _FakeBox:
        box = _FakeBox()
        boxes["gold"] = box
        return box

    monkeypatch.setattr(app_module.st, "status", _fake_status)

    callback = app_module._make_progress_adapter()
    callback("gold", "🏅 Gold — pergunta 1")
    callback("gold", "⚠️ Gold concluído com aviso (2.0s)")

    assert boxes["gold"].updated == [("⚠️ Gold concluído com aviso (2.0s)", "error")]


# ---------------------------------------------------------------------------
# App boot smoke test (AppTest — runs the real Streamlit script in-process)
# ---------------------------------------------------------------------------


def test_app_boots_without_exception() -> None:
    """The app must import and render top-to-bottom with no unhandled exception.

    This is the test that would have caught `ModuleNotFoundError: No module named
    'ai_etl'` (a broken editable install) and the `TypeError: 'NoneType' object is
    not subscriptable` (an `err[:80]` call on a None error message) — both were only
    visible once the script actually ran, not from unit-testing individual functions.
    """
    at = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "app.py"))
    at.run(timeout=30)

    assert not at.exception


def test_app_shows_sign_in_gate_when_unauthenticated() -> None:
    """Per ADR-006, `main()` gates on `_render_sign_in_gate()` and returns before
    rendering the welcome screen or pipeline UI when no session token is present."""
    at = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "app.py"))
    at.run(timeout=30)

    assert not at.exception
    info_body = " ".join(m.value for m in at.info)
    assert "login" in info_body.lower()
    body = " ".join(m.value for m in at.markdown)
    assert "Faça upload" not in body


def test_app_renders_welcome_screen_with_no_upload(monkeypatch) -> None:
    """Once the sign-in gate resolves to a `tenant_id`, `main()` proceeds past
    it and renders the welcome screen, matching this test's pre-ADR-006
    behavior — only the auth boundary changed.

    `_render_sign_in_gate` is mocked as a whole (rather than driving the real
    `st.text_input` widget through `verify_session_token`/`ensure_user`) to
    keep this test scoped to "what renders once authenticated," not to the
    gate's own widget/token mechanics — those are covered separately by
    `test_app_shows_sign_in_gate_when_unauthenticated` and by
    `test_auth_service.py`. Two prior attempts at driving the real widget
    (`.text_input(...).set_value(...)` + a second `.run()`, and pre-seeding
    `session_state["clerk_session_token"]` before a single `.run()`) both left
    `main()` returning early with no exception recorded, for a reason not
    reproducible locally — this sandbox hangs on any script that constructs a
    real `AppTest`, streamlit-testing or not, so it could only be debugged via
    CI round-trips. Mocking at this boundary removes that uncertainty
    entirely rather than continuing to guess at it."""
    monkeypatch.setattr(app_module, "_render_sign_in_gate", lambda: "user_test_123")
    at = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "app.py"))
    at.run(timeout=30)

    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Faça upload" in body or "upload" in body.lower()


@pytest.mark.parametrize("has_key", [True, False])
def test_check_api_key_reflects_env_var(monkeypatch, has_key: bool) -> None:
    if has_key:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    else:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert app_module._check_api_key() is has_key
