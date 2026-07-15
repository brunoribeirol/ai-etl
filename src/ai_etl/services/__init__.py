"""Presentation-agnostic orchestration services.

Nothing under this package may import `streamlit` or touch any Streamlit API
(`st.session_state`, `st.status`, `st.error`, ...). Callers (the Streamlit app,
the CLI, tests) drive these services with plain Python inputs/outputs and, where
they want live progress, a `ProgressCallback` — see `pipeline_service`.
"""
