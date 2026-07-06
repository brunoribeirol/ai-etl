"""AI-ETL — Agentic Business Intelligence.

Upload messy data. Ask a business question.
Get a clean pipeline + visual insights — automatically.

Run with:
    streamlit run app.py   (or: make app)
"""

import contextlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="AI-ETL · Agentic BI",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RUNS_DIR = Path("runs")
RUNS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = RUNS_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

SEVERITY_BADGE = {"ok": "🟢 ok", "warning": "🟡 warning", "error": "🔴 error"}

EXAMPLE_QUESTIONS = [
    "Quais produtos têm maior volume de vendas?",
    "Qual será a tendência de vendas nos próximos meses?",
    "Quais clientes têm maior risco de churn?",
    "Quais regiões devo priorizar para crescer?",
    "Como segmentar os clientes por perfil de compra?",
]

AGENT_STEPS = {
    "orchestrator": ("🧠", "Orchestrator", "Planejando o pipeline..."),
    "extractor": ("📥", "Extractor", "Extraindo e inspecionando os dados..."),
    "transformer": ("⚙️", "Transformer", "Transformando e limpando os dados (Silver)..."),
    "quality": ("🔍", "Quality", "Verificando qualidade dos dados..."),
    "loader": ("💾", "Loader", "Persistindo os dados limpos..."),
}

PRIORITY_ICON = {"high": "🔴", "medium": "🟡", "low": "🟢"}
PRIORITY_LABEL = {"high": "Alta", "medium": "Média", "low": "Baixa"}

LLM_MODEL = os.getenv("AI_ETL_LLM_MODEL", "gpt-4o-mini")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _check_api_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def _read_uploaded_file(uploaded_file) -> Optional[pd.DataFrame]:
    name = uploaded_file.name.lower()
    try:
        if name.endswith(".csv"):
            return pd.read_csv(uploaded_file)
        elif name.endswith((".xlsx", ".xls")):
            return pd.read_excel(uploaded_file)
        elif name.endswith(".json"):
            return pd.read_json(uploaded_file)
    except Exception as exc:
        st.error(f"Erro ao ler o arquivo: {exc}")
    return None


def _save_upload_to_temp(uploaded_file) -> Path:
    suffix = Path(uploaded_file.name).suffix
    dest = UPLOADS_DIR / f"{uuid.uuid4()}{suffix}"
    dest.write_bytes(uploaded_file.getvalue())
    return dest


def _auto_generate_spec(
    file_path: Path,
    df: pd.DataFrame,
    output_csv: Path,
    business_question: str = "",
) -> str:
    cols = ", ".join(df.columns.tolist())
    n_rows, n_cols = df.shape
    question_hint = f" A análise responderá: {business_question}." if business_question else ""
    return (
        f"Read the file at {file_path}. "
        f"The file has {n_rows} rows and {n_cols} columns: {cols}.{question_hint} "
        f"Clean the data: remove completely duplicate rows, standardize column names to snake_case, "
        f"parse date columns where obvious, fill obvious missing values, "
        f"preserve all columns and their values. "
        f"Save the cleaned result to {output_csv}."
    )


# ---------------------------------------------------------------------------
# Pipeline runners
# ---------------------------------------------------------------------------
def _run_silver_pipeline(spec: str) -> dict:
    from ai_etl.audit.db import save_run
    from ai_etl.core.graph import build_graph
    from ai_etl.core.state import initial_state

    run_id = str(uuid.uuid4())
    state = initial_state(spec=spec, run_id=run_id)
    graph = build_graph()

    final_state = dict(state)
    agent_timings: dict[str, float] = {}

    with st.status("⚡ Executando pipeline Silver...", expanded=True) as status_box:
        t_total = time.time()
        for chunk in graph.stream(state):
            node_name = list(chunk.keys())[0]
            partial = chunk[node_name]
            t_node = time.time()
            final_state.update(partial)

            emoji, label, desc = AGENT_STEPS.get(node_name, ("⚡", node_name, "Processando..."))
            agent_timings[node_name] = round(time.time() - t_node, 2)
            elapsed = round(time.time() - t_total, 1)
            status_box.write(f"{emoji} **{label}** — {desc} *({elapsed}s)*")

        overall = final_state.get("status", "unknown")
        total_time = round(time.time() - t_total, 1)
        if overall == "completed":
            status_box.update(label=f"✅ Silver concluído em {total_time}s", state="complete")
        else:
            err = final_state.get("error", "Erro desconhecido")
            status_box.update(label=f"❌ Silver falhou: {err[:80]}", state="error")

    final_state["_agent_timings"] = agent_timings
    final_state["_total_time"] = total_time

    save_run(final_state, log_dir=str(RUNS_DIR))  # type: ignore[arg-type]
    return final_state


def _run_gold_analysis(silver_df: pd.DataFrame, business_question: str) -> dict:
    from ai_etl.agents.analyst import run_analyst

    with st.status("🏅 Gold — análise descritiva...", expanded=True) as status_box:
        status_box.write("🤖 **Analyst Agent** — Calculando KPIs e insights...")
        t0 = time.time()
        result = run_analyst(silver_df, business_question)
        elapsed = round(time.time() - t0, 1)
        attempts = result.get("attempts", 1)

        if result["error"]:
            status_box.update(label=f"⚠️ Gold concluído com aviso ({elapsed}s)", state="error")
        else:
            status_box.update(
                label=f"✅ Gold pronto em {elapsed}s ({attempts} tentativa(s))", state="complete"
            )
    return result


def _run_science_analysis(silver_df: pd.DataFrame, business_question: str) -> dict:
    from ai_etl.agents.science import run_science

    with st.status("🔬 Science — análise preditiva...", expanded=True) as status_box:
        status_box.write("🤖 **Science Agent** — Treinando modelo e gerando previsões...")
        t0 = time.time()
        result = run_science(silver_df, business_question)
        elapsed = round(time.time() - t0, 1)
        attempts = result.get("attempts", 1)

        if result["error"]:
            status_box.update(label=f"⚠️ Science concluído com aviso ({elapsed}s)", state="error")
        else:
            model_type = result.get("model_info", {}).get("model_type", "Modelo")
            status_box.update(
                label=f"✅ {model_type} treinado em {elapsed}s ({attempts} tentativa(s))",
                state="complete",
            )
    return result


def _run_advisor_analysis(
    silver_df: pd.DataFrame,
    business_question: str,
    gold_result: dict,
    science_result: dict,
) -> dict:
    from ai_etl.agents.advisor import run_advisor

    with st.status("🎯 Advisor — recomendações prescritivas...", expanded=True) as status_box:
        status_box.write("🤖 **Advisor Agent** — Sintetizando análises e gerando recomendações...")
        t0 = time.time()
        result = run_advisor(silver_df, business_question, gold_result, science_result)
        elapsed = round(time.time() - t0, 1)
        n = len(result.get("recommendations", []))

        if result["error"]:
            status_box.update(label=f"⚠️ Advisor com aviso ({elapsed}s)", state="error")
        else:
            status_box.update(label=f"✅ {n} recomendações geradas em {elapsed}s", state="complete")
    return result


def _load_history() -> pd.DataFrame:
    db_path = RUNS_DIR / "runs.db"
    if not db_path.exists():
        return pd.DataFrame()
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        try:
            return pd.read_sql(
                "SELECT run_id, status, rows_loaded, timestamp, substr(spec,1,80) as spec "
                "FROM runs ORDER BY timestamp DESC LIMIT 20",
                conn,
            )
        except Exception:
            return pd.DataFrame()


# ---------------------------------------------------------------------------
# Result rendering
# ---------------------------------------------------------------------------
def _render_results(result: dict) -> None:
    bronze_df: Optional[pd.DataFrame] = result.get("bronze")
    state: dict = result.get("state", {})
    gold: dict = result.get("gold", {})
    science: dict = result.get("science", {})
    advisor: dict = result.get("advisor", {})
    question: str = result.get("question", "")

    silver_df = state.get("transformed_data")
    status = state.get("status", "unknown")
    run_id = state.get("run_id", "—")
    total_time = state.get("_total_time", "—")

    # Status banner
    if status == "completed":
        load_result = state.get("load_result") or {}
        rows = load_result.get("rows_loaded", "—")
        st.success(f"✅ Pipeline concluído em {total_time}s — {rows} linhas Silver")
    elif status == "failed":
        err = state.get("error", "Erro desconhecido")
        st.error(f"❌ Pipeline falhou: {err}")
    else:
        st.error(f"❌ Status: {state.get('error', '—')}")

    st.caption(f"Run ID: `{run_id}`")
    st.divider()

    # Tabs
    (
        tab_advisor,
        tab_gold,
        tab_science,
        tab_silver,
        tab_bronze,
        tab_pipeline,
        tab_code,
    ) = st.tabs(
        [
            "🎯 Recomendações",
            "🏅 Gold — Insights",
            "🔬 Science — Previsões",
            "🥈 Silver — Dados limpos",
            "🥉 Bronze — Dados brutos",
            "🔍 Pipeline",
            "⚙️ Código gerado",
        ]
    )

    # ── ADVISOR ───────────────────────────────────────────────────────────
    with tab_advisor:
        st.markdown(f'### 🎯 O que fazer sobre: *"{question}"*')
        st.divider()

        if not advisor:
            st.info("Recomendações não disponíveis — análise anterior falhou.")
        elif advisor.get("error") and not advisor.get("recommendations"):
            st.error(f"Não foi possível gerar recomendações: `{advisor['error']}`")
        else:
            if advisor.get("summary"):
                st.info(f"**Resumo executivo:** {advisor['summary']}")
                st.divider()

            recs = advisor.get("recommendations", [])
            for i, rec in enumerate(recs, 1):
                priority = rec.get("priority", "medium")
                icon = PRIORITY_ICON.get(priority, "⚪")
                label = PRIORITY_LABEL.get(priority, priority.capitalize())

                with st.container(border=True):
                    col1, col2 = st.columns([6, 1])
                    with col1:
                        st.markdown(f"**{i}. {rec.get('action', '—')}**")
                        st.caption(f"Justificativa: {rec.get('rationale', '—')}")
                        st.caption(f"Impacto esperado: {rec.get('expected_impact', '—')}")
                    with col2:
                        st.markdown(f"{icon} **{label}**")

    # ── GOLD ──────────────────────────────────────────────────────────────
    with tab_gold:
        st.markdown(f"### ❓ {question}")
        st.divider()

        if not gold:
            st.warning("Análise Gold não executada — o pipeline Silver falhou.")
        elif gold.get("error") and not gold.get("fig"):
            st.error(
                f"**Não foi possível gerar a análise após 3 tentativas.**\n\n"
                f"Erro: `{gold['error']}`\n\n"
                "Sugestão: reformule a pergunta com nomes de colunas específicos."
            )
            if gold.get("code"):
                with st.expander("Ver código que falhou"):
                    st.code(gold["code"], language="python")
        else:
            if gold.get("narrative"):
                st.info(f"💡 **Insight:** {gold['narrative']}")

            if gold.get("fig"):
                st.plotly_chart(gold["fig"], use_container_width=True)

            gold_df: Optional[pd.DataFrame] = gold.get("gold_df")
            if gold_df is not None and not gold_df.empty:
                st.markdown("**Dados agregados:**")
                st.dataframe(gold_df, use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇️ Baixar dados Gold (CSV)",
                    data=gold_df.to_csv(index=False).encode(),
                    file_name=f"{run_id}_gold.csv",
                    mime="text/csv",
                    key=f"dl_gold_{run_id}",
                )

            st.caption(f"Gerado em {gold.get('attempts', 1)} tentativa(s).")

    # ── SCIENCE ───────────────────────────────────────────────────────────
    with tab_science:
        st.markdown("### 🔬 Análise Preditiva")
        st.divider()

        if not science:
            st.info("Análise preditiva não executada.")
        elif science.get("error") and not science.get("fig"):
            st.warning(
                f"Não foi possível executar o modelo preditivo.\n\n"
                f"Causa: `{science['error']}`\n\n"
                "Dica: certifique-se de que os dados têm coluna de data ou variável alvo clara."
            )
            if science.get("code"):
                with st.expander("Ver código que falhou"):
                    st.code(science["code"], language="python")
        else:
            model_info = science.get("model_info", {})
            if model_info:
                task_labels = {
                    "forecast": "Previsão de Série Temporal",
                    "regression": "Regressão",
                    "classification": "Classificação",
                    "clustering": "Clusterização",
                }
                task = task_labels.get(model_info.get("task", ""), model_info.get("task", ""))
                model_name = model_info.get("model_type", "—")
                metrics = model_info.get("metrics", {})

                m_cols = st.columns(2 + len(metrics))
                m_cols[0].metric("Tipo de análise", task)
                m_cols[1].metric("Modelo", model_name)
                for i, (k, v) in enumerate(metrics.items()):
                    m_cols[2 + i].metric(k.upper(), f"{v:.3f}" if isinstance(v, float) else v)

                st.divider()

            if science.get("narrative"):
                st.info(f"💡 **Insight preditivo:** {science['narrative']}")

            if science.get("fig"):
                st.plotly_chart(science["fig"], use_container_width=True)

            pred_df: Optional[pd.DataFrame] = science.get("predictions_df")
            if pred_df is not None and not pred_df.empty:
                st.markdown("**Dados do modelo:**")
                st.dataframe(pred_df.head(100), use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇️ Baixar previsões (CSV)",
                    data=pred_df.to_csv(index=False).encode(),
                    file_name=f"{run_id}_science.csv",
                    mime="text/csv",
                    key=f"dl_science_{run_id}",
                )

            st.caption(f"Gerado em {science.get('attempts', 1)} tentativa(s).")

    # ── SILVER ────────────────────────────────────────────────────────────
    with tab_silver:
        st.markdown("### Dados limpos e padronizados")
        if silver_df is not None and isinstance(silver_df, pd.DataFrame) and not silver_df.empty:
            n_rows, n_cols = silver_df.shape

            report = state.get("quality_report") or {}
            sev = report.get("severity", "ok")
            summary_txt = report.get("summary", "")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Linhas", f"{n_rows:,}")
            c2.metric("Colunas", n_cols)
            c3.metric("Qualidade", SEVERITY_BADGE.get(sev, sev))
            c4.metric("Nulos totais", int(silver_df.isna().sum().sum()))
            if summary_txt:
                st.caption(summary_txt)

            st.divider()
            st.dataframe(silver_df.head(200), use_container_width=True)
            if n_rows > 200:
                st.caption(f"Exibindo as primeiras 200 de {n_rows:,} linhas.")

            st.download_button(
                "⬇️ Baixar Silver (CSV)",
                data=silver_df.to_csv(index=False).encode(),
                file_name=f"{run_id}_silver.csv",
                mime="text/csv",
                key=f"dl_silver_{run_id}",
            )
        else:
            st.warning("Dados Silver não disponíveis.")

    # ── BRONZE ────────────────────────────────────────────────────────────
    with tab_bronze:
        st.markdown("### Dados brutos originais")
        if bronze_df is not None and isinstance(bronze_df, pd.DataFrame):
            n_rows_b, n_cols_b = bronze_df.shape
            c1, c2, c3 = st.columns(3)
            c1.metric("Linhas brutas", f"{n_rows_b:,}")
            c2.metric("Colunas", n_cols_b)
            c3.metric("Nulos", int(bronze_df.isna().sum().sum()))
            st.divider()
            st.dataframe(bronze_df.head(200), use_container_width=True)
            with st.expander("Tipos de dados originais"):
                dtype_df = pd.DataFrame(
                    {"coluna": bronze_df.columns, "tipo": bronze_df.dtypes.astype(str).values}
                )
                st.dataframe(dtype_df, hide_index=True, use_container_width=True)
        else:
            st.info("Bronze disponível apenas para uploads de arquivo.")

    # ── PIPELINE ──────────────────────────────────────────────────────────
    with tab_pipeline:
        st.markdown("### Execução dos Agentes Silver")
        timings = state.get("_agent_timings", {})
        for node, (emoji, label, _) in AGENT_STEPS.items():
            t = timings.get(node)
            t_str = f"{t}s" if t is not None else "—"
            node_error = None
            if state.get("status") == "failed" and node == "transformer":
                node_error = state.get("transformation_error")

            if node_error:
                st.error(f"{emoji} **{label}** — {t_str} — ❌ Falhou")
                with st.expander("Ver erro"):
                    st.code(node_error)
            else:
                st.success(f"{emoji} **{label}** — {t_str}")

        st.divider()
        plan = state.get("pipeline_plan") or {}
        if plan:
            st.markdown("### Plano do Pipeline")
            st.json(plan)

        report = state.get("quality_report") or {}
        if report:
            st.divider()
            st.markdown("### Relatório de Qualidade")
            for check in report.get("checks", []):
                check_type = check.get("check", "—")
                sev = check.get("severity", "ok")
                badge = SEVERITY_BADGE.get(sev, sev)
                col = check.get("column", "global")
                if check_type == "null":
                    detail = f"razão de nulos: {check.get('null_ratio', 0):.1%}"
                elif check_type == "duplicate":
                    detail = f"duplicatas: {check.get('count', 0)}"
                elif check_type == "outlier":
                    detail = f"outliers: {check.get('outlier_count', 0)}"
                else:
                    detail = str(check)
                st.write(f"{badge} **{check_type}** em `{col}` — {detail}")

    # ── CÓDIGO ────────────────────────────────────────────────────────────
    with tab_code:
        silver_code = state.get("transformation_code", "")
        if silver_code:
            st.markdown("### Código Silver — Transformer Agent")
            st.caption(f"Gerado em {state.get('transformation_attempts', 0)} tentativa(s)")
            st.code(silver_code, language="python")
            st.download_button(
                "⬇️ Baixar transform.py",
                data=silver_code,
                file_name=f"{run_id}_transform.py",
                mime="text/x-python",
                key=f"dl_transform_{run_id}",
            )

        gold_code = gold.get("code", "")
        if gold_code:
            st.divider()
            st.markdown("### Código Gold — Analyst Agent")
            st.caption(f"Gerado em {gold.get('attempts', 1)} tentativa(s)")
            st.code(gold_code, language="python")

        science_code = science.get("code", "")
        if science_code:
            st.divider()
            st.markdown("### Código Science — Science Agent")
            st.caption(f"Gerado em {science.get('attempts', 1)} tentativa(s)")
            st.code(science_code, language="python")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown("## ⚡ AI-ETL")
        st.caption("Agentic Business Intelligence")
        st.divider()

        if not _check_api_key():
            st.error("⚠️ **OPENAI_API_KEY** não configurada.\nCrie um `.env` com a chave.")
        else:
            st.success("✅ API configurada")
            st.caption(f"Modelo: `{LLM_MODEL}`")

        st.divider()
        st.markdown("### Pirâmide analítica")
        st.markdown(
            "🥉 **Bronze** — dado bruto, sem toque\n\n"
            "🥈 **Silver** — limpo, padronizado, validado\n\n"
            "🏅 **Gold** — KPIs e insights descritivos\n\n"
            "🔬 **Science** — previsões e modelos preditivos\n\n"
            "🎯 **Advisor** — recomendações de ação"
        )

        st.divider()
        st.markdown("### Como funciona")
        st.markdown(
            "**1.** Upload de CSV, Excel ou JSON\n\n"
            "**2.** Escreva o que você quer saber\n\n"
            "**3.** 5 agentes limpam os dados (Silver)\n\n"
            "**4.** Analyst gera gráfico + insight (Gold)\n\n"
            "**5.** Science Agent treina modelo preditivo\n\n"
            "**6.** Advisor sintetiza e recomenda ações"
        )

        st.divider()
        st.caption("Formatos: CSV · Excel · JSON")


# ---------------------------------------------------------------------------
# Welcome screen
# ---------------------------------------------------------------------------
def _render_welcome() -> None:
    st.markdown(
        """
        <div style="text-align: center; padding: 2rem 1rem;">
            <h2>🚀 Seu time de dados, movido a IA</h2>
            <p style="font-size: 1.1rem; color: #888; max-width: 600px; margin: 0 auto;">
                Faça upload dos seus dados — sujos, bagunçados, como estiverem.<br>
                Escreva o que você quer saber.<br>
                Os agentes organizam, analisam, preveem e recomendam.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown("### 🥉 Bronze\n*Ingestão raw*")
    c2.markdown("### 🥈 Silver\n*5 agentes ETL*")
    c3.markdown("### 🏅 Gold\n*KPIs e gráficos*")
    c4.markdown("### 🔬 Science\n*Modelos preditivos*")
    c5.markdown("### 🎯 Advisor\n*Recomendações*")

    st.divider()
    st.info(
        "👆 **Para começar:** faça upload de um arquivo CSV/Excel na aba **▶️ Analisar** "
        "e escreva sua pergunta de negócio.",
        icon="💡",
    )


# ---------------------------------------------------------------------------
# Main tab: Analisar
# ---------------------------------------------------------------------------
def _tab_executar() -> None:
    col_input, _ = st.columns([3, 1])

    with col_input:
        st.markdown("### 1. Fonte de dados")
        source_mode = st.radio(
            "Fonte",
            ["📁 Upload de arquivo", "📝 Especificação manual"],
            horizontal=True,
            label_visibility="collapsed",
        )

        uploaded_file = None
        df_bronze = None

        if source_mode == "📁 Upload de arquivo":
            uploaded_file = st.file_uploader(
                "Arraste ou selecione um arquivo",
                type=["csv", "xlsx", "xls", "json"],
                help="Suporte: CSV, Excel (.xlsx/.xls), JSON",
            )
            if uploaded_file:
                df_bronze = _read_uploaded_file(uploaded_file)
                if df_bronze is not None:
                    n_rows, n_cols = df_bronze.shape
                    st.caption(
                        f"✅ **{uploaded_file.name}** — {n_rows:,} linhas × {n_cols} colunas"
                    )
                    with st.expander("👀 Preview (Bronze)"):
                        st.dataframe(df_bronze.head(8), use_container_width=True)

        st.markdown("### 2. O que você quer saber?")
        st.caption("Exemplos:")
        example_cols = st.columns(min(len(EXAMPLE_QUESTIONS), 5))
        chosen_example = None
        for col, q in zip(example_cols, EXAMPLE_QUESTIONS):
            if col.button(q[:28] + "…", use_container_width=True, help=q):
                chosen_example = q

        business_question = st.text_area(
            "Pergunta de negócio",
            value=chosen_example or st.session_state.get("last_question", ""),
            height=80,
            placeholder="Ex: Quais produtos devo priorizar para aumentar o faturamento?",
            label_visibility="collapsed",
        )

        if source_mode == "📝 Especificação manual":
            with st.expander("⚙️ Especificação técnica do pipeline"):
                manual_spec = st.text_area(
                    "Spec",
                    height=120,
                    placeholder="Ex: Read case_study/data/sales.csv. Save to runs/output.csv.",
                    label_visibility="collapsed",
                )
        else:
            manual_spec = ""

        st.divider()
        can_run = (
            _check_api_key()
            and bool(business_question.strip())
            and (uploaded_file is not None or bool(manual_spec.strip()))
        )

        if not _check_api_key():
            st.warning("Configure `OPENAI_API_KEY` no arquivo `.env` para executar.")
        elif not (uploaded_file or manual_spec.strip()):
            st.caption("⬆️ Faça upload de um arquivo para continuar.")

        if st.button(
            "▶️ Analisar dados",
            type="primary",
            disabled=not can_run,
            use_container_width=True,
        ):
            st.session_state["last_question"] = business_question
            st.session_state["pipeline_result"] = None

            if uploaded_file and df_bronze is not None:
                saved_path = _save_upload_to_temp(uploaded_file)
                output_csv = RUNS_DIR / f"{uuid.uuid4()}_silver.csv"
                spec = _auto_generate_spec(
                    saved_path, df_bronze, output_csv, business_question.strip()
                )
            else:
                spec = manual_spec.strip()
                df_bronze = None

            # Silver pipeline
            silver_state = _run_silver_pipeline(spec)
            silver_df = silver_state.get("transformed_data")

            gold_result: dict = {}
            science_result: dict = {}
            advisor_result: dict = {}

            if isinstance(silver_df, pd.DataFrame) and not silver_df.empty:
                q = business_question.strip()
                gold_result = _run_gold_analysis(silver_df, q)
                science_result = _run_science_analysis(silver_df, q)
                advisor_result = _run_advisor_analysis(silver_df, q, gold_result, science_result)
            elif silver_state.get("status") != "completed":
                st.warning("Pipeline Silver não completou — análise não executada.")

            st.session_state["pipeline_result"] = {
                "state": silver_state,
                "bronze": df_bronze,
                "gold": gold_result,
                "science": science_result,
                "advisor": advisor_result,
                "question": business_question.strip(),
            }
            st.rerun()

    if st.session_state.get("pipeline_result"):
        st.divider()
        _render_results(st.session_state["pipeline_result"])


# ---------------------------------------------------------------------------
# History tab
# ---------------------------------------------------------------------------
def _tab_historico() -> None:
    st.markdown("### Histórico de execuções")

    history = _load_history()
    if history.empty:
        st.info("Nenhuma execução registrada. Execute um pipeline para começar.")
        return

    total = len(history)
    completed = (history["status"] == "completed").sum()
    failed = (history["status"] == "failed").sum()

    m1, m2, m3 = st.columns(3)
    m1.metric("Total de runs", total)
    m2.metric("✅ Concluídos", int(completed))
    m3.metric("❌ Falhos", int(failed))

    st.divider()

    def _style_status(val: str) -> str:
        if val == "completed":
            return "color: green; font-weight: bold"
        if val == "failed":
            return "color: red; font-weight: bold"
        return ""

    st.dataframe(
        history.style.map(_style_status, subset=["status"]),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    selected_run = st.selectbox(
        "Inspecionar run:", history["run_id"].tolist(), label_visibility="visible"
    )
    if selected_run:
        json_path = RUNS_DIR / f"{selected_run}.json"
        transform_path = RUNS_DIR / f"{selected_run}_transform.py"
        if json_path.exists():
            with open(json_path) as f:
                run_data = json.load(f)
            with st.expander("Ver JSON completo"):
                st.json(run_data)
        if transform_path.exists():
            code = transform_path.read_text()
            with st.expander("Ver código de transformação"):
                st.code(code, language="python")
            st.download_button(
                "⬇️ Baixar transform.py",
                data=code,
                file_name=f"{selected_run}_transform.py",
                mime="text/x-python",
                key=f"dl_hist_{selected_run}",
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    _render_sidebar()

    st.title("⚡ AI-ETL · Agentic Business Intelligence")
    st.caption(
        "Dado bruto → ETL auditável → insights descritivos → modelos preditivos → recomendações."
    )

    tab_run, tab_history = st.tabs(["▶️ Analisar", "📋 Histórico"])

    with tab_run:
        if not st.session_state.get("pipeline_result"):
            _render_welcome()
            st.divider()
        _tab_executar()

    with tab_history:
        _tab_historico()


if __name__ == "__main__":
    main()
