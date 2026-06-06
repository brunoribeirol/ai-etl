---
title: AI-ETL — Arquitetura do Framework
type: project
tags: [tcc, artefato, arquitetura, langgraph, agentes]
project: tcc-ai-etl
created: 2026-06-05
updated: 2026-06-06
status: active
---

# AI-ETL — Arquitetura do Framework

> Documento de referência para implementação. Claude Code e Codex devem ler este arquivo antes de qualquer tarefa de código.
> Ver também: [[decisions]], [[requirements]], [[tech-stack]], [[repo-structure]]

---

## 1. Fluxo Geral

```
[Usuário: spec em linguagem natural]
           ↓
    ┌──────────────┐
    │ Orchestrator │  → parseia spec, monta PipelineState inicial
    └──────────────┘
           ↓
    ┌──────────────┐
    │  Extractor   │  → conecta fontes, extrai dados e schemas
    └──────────────┘
           ↓
    ┌──────────────┐
    │ Transformer  │  → gera código Python via LLM, executa no sandbox
    └──────────────┘
           ↓
    ┌──────────────┐
    │    Quality   │  → detecta problemas, gera report com severidade
    └──────────────┘
        ↓         ↓
    severity=ok   severity=error
        ↓               ↓
    ┌────────┐      pipeline
    │ Loader │      interrompido
    └────────┘
        ↓
    [Destino: CSV ou PostgreSQL]
    [Audit log: JSON + SQLite]
```

### Regra de roteamento Quality → Loader

```python
def route_after_quality(state: PipelineState) -> str:
    if state["quality_report"]["severity"] == "error":
        return END  # pipeline interrompido
    return "loader"
```

---

## 2. PipelineState — Contrato Central

Todos os agentes leem e escrevem neste TypedDict. Nenhum agente comunica diretamente com outro — tudo passa pelo estado.

```python
from typing import TypedDict, Optional
import pandas as pd

class PipelineState(TypedDict):
    # --- Entrada ---
    spec: str                          # spec em linguagem natural (imutável)
    run_id: str                        # UUID gerado no início do run

    # --- Saída do Orchestrator ---
    pipeline_plan: dict                # estrutura abaixo
    # pipeline_plan = {
    #   "sources": [
    #     {"name": "orders", "type": "csv", "path": "data/orders.csv"},
    #     {"name": "customers", "type": "postgres", "table": "public.customers"}
    #   ],
    #   "destination": {"type": "postgres", "table": "public.output"},
    #   "transformations": ["rename column X to Y", "filter where status = active", "join on customer_id"],
    #   "quality_checks": ["null check on customer_id", "dedup on order_id"]
    # }

    # --- Saída do Extractor ---
    extracted_data: dict               # {"source_name": pd.DataFrame}
    source_schemas: dict               # {"source_name": {"columns": [...], "dtypes": {...}, "sample": [...]}}

    # --- Saída do Transformer ---
    transformation_code: str           # código Python gerado pelo LLM (função transform(dfs) → pd.DataFrame)
    transformed_data: Optional[object] # pd.DataFrame resultante (None até o Transformer rodar)
    transformation_attempts: int       # contador de tentativas (máx 3)
    transformation_error: Optional[str]# último erro de execução no sandbox

    # --- Saída do Quality Agent ---
    quality_report: dict               # estrutura abaixo
    # quality_report = {
    #   "checks": [
    #     {"check": "null_check", "column": "customer_id", "null_count": 5, "severity": "warning"},
    #     {"check": "duplicate_check", "duplicate_count": 0, "severity": "ok"},
    #   ],
    #   "severity": "ok" | "warning" | "error",  # máximo entre os checks individuais
    #   "summary": "2 checks executados, 1 warning, 0 errors"
    # }

    # --- Saída do Loader ---
    load_result: Optional[dict]        # {"rows_loaded": int, "destination": str, "timestamp": str}

    # --- Audit (preenchido incrementalmente por todos os agentes) ---
    audit_log: list                    # lista de dicts com ações de cada agente

    # --- Controle de fluxo ---
    error: Optional[str]               # mensagem de erro fatal (seta para END se preenchido)
    status: str                        # "running" | "completed" | "failed"
```

---

## 3. Grafo LangGraph

```python
from langgraph.graph import StateGraph, END
from src.agents.orchestrator import orchestrator_node
from src.agents.extractor import extractor_node
from src.agents.transformer import transformer_node
from src.agents.quality import quality_node
from src.agents.loader import loader_node

def build_graph() -> StateGraph:
    graph = StateGraph(PipelineState)

    # Nós
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("extractor", extractor_node)
    graph.add_node("transformer", transformer_node)
    graph.add_node("quality", quality_node)
    graph.add_node("loader", loader_node)

    # Arestas sequenciais
    graph.set_entry_point("orchestrator")
    graph.add_edge("orchestrator", "extractor")
    graph.add_edge("extractor", "transformer")
    graph.add_edge("transformer", "quality")

    # Aresta condicional após Quality
    graph.add_conditional_edges(
        "quality",
        route_after_quality,
        {"loader": "loader", END: END}
    )
    graph.add_edge("loader", END)

    return graph.compile()
```

---

## 4. Especificação de Cada Agente

### 4.1 Orchestrator Agent

**Responsabilidade:** parsear a spec em linguagem natural e estruturar o `pipeline_plan`.

**Entrada do estado:** `spec`, `run_id`

**Saída para o estado:** `pipeline_plan`

**Usa LLM:** sim — para parsear NL em JSON estruturado.

**Prompt template:**

```
You are a data pipeline planner. The user provided the following pipeline specification:

"{spec}"

Extract a structured pipeline plan in JSON with these fields:
- sources: list of data sources (each with name, type [csv/postgres/rest], and connection details)
- destination: target where results should be loaded (type [csv/postgres], and details)
- transformations: list of transformation descriptions in plain English
- quality_checks: list of quality checks to run (infer from spec or use defaults: null, duplicate, type, outlier)

Available source types: csv, postgres, rest
Available destination types: csv, postgres

Respond ONLY with valid JSON. No explanation.
```

**Pseudocódigo:**

```python
def orchestrator_node(state: PipelineState) -> PipelineState:
    response = llm.invoke(ORCHESTRATOR_PROMPT.format(spec=state["spec"]))
    pipeline_plan = json.loads(response.content)
    audit_entry = {"agent": "orchestrator", "action": "plan_created", ...}
    return {**state, "pipeline_plan": pipeline_plan, "audit_log": [..., audit_entry]}
```

**Erros possíveis:**
- LLM retorna JSON inválido → tentar novamente (máx 2 vezes) com mensagem de erro no prompt
- Fonte ou destino não suportado → setar `error` e finalizar

---

### 4.2 Extractor Agent

**Responsabilidade:** conectar às fontes, extrair dados como DataFrames, extrair schemas.

**Entrada do estado:** `pipeline_plan.sources`

**Saída para o estado:** `extracted_data`, `source_schemas`

**Usa LLM:** não. Lógica determinística + conectores.

**Conectores v0:**
- `src/sources/csv_source.py` — `pd.read_csv(path)`
- `src/sources/postgres_source.py` — SQLAlchemy + `pd.read_sql(query, engine)`
- `src/sources/rest_source.py` — `requests.get(url)` → normaliza JSON para DataFrame

**Schema extraído:**
```python
{
    "columns": df.columns.tolist(),
    "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
    "shape": df.shape,
    "sample": df.head(5).to_dict(orient="records"),
    "null_counts": df.isnull().sum().to_dict()
}
```

**Erros possíveis:**
- Arquivo não encontrado → `error` = "Source not found: {path}"
- Conexão PostgreSQL falhou → `error` = "PostgreSQL connection failed: {msg}"
- REST API retornou não-200 → `error` = "REST API error: {status_code}"

---

### 4.3 Transformer Agent

**Responsabilidade:** gerar código Python de transformação, executar no sandbox, iterar em caso de erro.

**Entrada do estado:** `pipeline_plan.transformations`, `source_schemas`, `extracted_data`

**Saída para o estado:** `transformation_code`, `transformed_data`, `transformation_attempts`

**Usa LLM:** sim — geração de código.

**Prompt template:**

```
You are a Python data transformation expert.

Pipeline specification:
{transformations}

Available DataFrames (already loaded):
{schema_summary}

Write a Python function with this exact signature:
```python
def transform(dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # dfs keys: {source_names}
    ...
    return result_df
```

Rules:
- Use only pandas and numpy. No other imports.
- The function must return a single pd.DataFrame.
- Handle edge cases (empty DataFrames, missing columns).
- Do not read from files or databases — data is already in `dfs`.

Respond ONLY with the Python function. No explanation.
```

**Loop de tentativas:**
```python
MAX_ATTEMPTS = 3
for attempt in range(1, MAX_ATTEMPTS + 1):
    code = llm.invoke(prompt).content
    result, error = execute_in_sandbox(code, dfs)
    if error is None:
        break
    prompt += f"\nPrevious attempt failed with error:\n{error}\nFix the code."
```

**Sandbox (`src/core/sandbox.py`):**
```python
SAFE_GLOBALS = {
    "__builtins__": {
        "len": len, "range": range, "print": print,
        "int": int, "float": float, "str": str, "list": list,
        "dict": dict, "bool": bool, "None": None, "True": True, "False": False
    },
    "pd": pandas,
    "np": numpy,
}

def execute_in_sandbox(code: str, dfs: dict) -> tuple[pd.DataFrame | None, str | None]:
    local_env = {"dfs": dfs}
    try:
        exec(code, SAFE_GLOBALS.copy(), local_env)
        return local_env["transform"](dfs), None
    except Exception as e:
        return None, str(e)
```

**Limitação conhecida:** exec() com restricted globals pode ser bypassado via `().__class__.__mro__[1].__subclasses__()`. Aceitável para TCC com dados públicos. Ver `security.md`.

---

### 4.4 Quality Agent

**Responsabilidade:** executar checks de qualidade no DataFrame transformado.

**Entrada do estado:** `transformed_data`, `pipeline_plan.quality_checks`

**Saída para o estado:** `quality_report`

**Usa LLM:** não. Lógica determinística.

**Checks implementados em v0:**

```python
def check_nulls(df, threshold=0.05) -> dict:
    for col in df.columns:
        null_ratio = df[col].isnull().mean()
        if null_ratio > threshold:
            return {"check": "null", "column": col, "null_ratio": null_ratio,
                    "severity": "error" if null_ratio > 0.2 else "warning"}
    return {"check": "null", "severity": "ok"}

def check_duplicates(df) -> dict:
    dup_count = df.duplicated().sum()
    return {"check": "duplicate", "count": dup_count,
            "severity": "warning" if dup_count > 0 else "ok"}

def check_types(df, expected_schema) -> dict:
    # compara dtypes do df transformado com schema da fonte
    ...

def check_outliers_iqr(df) -> dict:
    # IQR para colunas numéricas
    for col in df.select_dtypes(include="number").columns:
        Q1, Q3 = df[col].quantile([0.25, 0.75])
        IQR = Q3 - Q1
        outlier_count = ((df[col] < Q1 - 1.5*IQR) | (df[col] > Q3 + 1.5*IQR)).sum()
        ...
```

**Severidade agregada:**
```python
SEVERITY_ORDER = {"ok": 0, "warning": 1, "error": 2}
severity = max(checks, key=lambda c: SEVERITY_ORDER[c["severity"]])["severity"]
```

**Routing:** se `severity == "error"` → pipeline interrompido, `status = "failed"`.

---

### 4.5 Loader Agent

**Responsabilidade:** carregar DataFrame validado no destino, validar contagem pós-carga.

**Entrada do estado:** `transformed_data`, `pipeline_plan.destination`, `quality_report`

**Saída para o estado:** `load_result`, `status = "completed"`

**Usa LLM:** não.

**Destinos v0:**

```python
# CSV
def load_csv(df, path):
    df.to_csv(path, index=False)
    return {"rows_loaded": len(df), "destination": path}

# PostgreSQL (sempre usa parâmetros bindados — nunca f-strings em SQL)
def load_postgres(df, table, engine):
    df.to_sql(table, engine, if_exists="replace", index=False)
    count = pd.read_sql(f"SELECT COUNT(*) FROM {table}", engine).iloc[0,0]
    assert count == len(df), f"Load count mismatch: expected {len(df)}, got {count}"
    return {"rows_loaded": count, "destination": table}
```

---

## 5. Audit Module (cross-cutting)

Não é um agente. É um módulo invocado por todos os agentes.

**`src/audit/logger.py`** — append ao `audit_log` no estado:
```python
def log_action(state: PipelineState, agent: str, action: str, details: dict) -> list:
    entry = {
        "agent": agent,
        "action": action,
        "timestamp": datetime.utcnow().isoformat(),
        "run_id": state["run_id"],
        "details": details
    }
    return state["audit_log"] + [entry]
```

**`src/audit/db.py`** — persiste no SQLite ao final:
```python
def save_run(state: PipelineState, db_path: str):
    # salva JSON completo do estado final
    # registra na tabela runs: run_id, spec, status, duration, timestamp
```

**Output por run:**
- `audit/<run_id>.json` — snapshot completo do estado final
- `audit/runs.db` — SQLite com histórico de todos os runs

---

## 6. Estrutura de Pastas (implementação)

```
ai-etl/
├── src/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── orchestrator.py   # orchestrator_node(state) → state
│   │   ├── extractor.py      # extractor_node(state) → state
│   │   ├── transformer.py    # transformer_node(state) → state
│   │   ├── quality.py        # quality_node(state) → state
│   │   └── loader.py         # loader_node(state) → state
│   ├── core/
│   │   ├── __init__.py
│   │   ├── graph.py          # build_graph() → CompiledGraph
│   │   ├── state.py          # PipelineState TypedDict
│   │   └── sandbox.py        # execute_in_sandbox(code, dfs) → (df, error)
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── csv_source.py     # load_csv(path) → DataFrame
│   │   ├── postgres_source.py# load_postgres(table, engine) → DataFrame
│   │   └── rest_source.py    # load_rest(url, params) → DataFrame
│   ├── destinations/
│   │   ├── __init__.py
│   │   ├── csv_dest.py       # save_csv(df, path) → dict
│   │   └── postgres_dest.py  # save_postgres(df, table, engine) → dict
│   └── audit/
│       ├── __init__.py
│       ├── logger.py         # log_action(state, agent, action, details) → list
│       └── db.py             # save_run(state, db_path)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── case_study/
├── audit/                    # JSON e SQLite gerados por cada run
├── .env                      # OPENAI_API_KEY, POSTGRES_URL (nunca commitar)
├── .env.example
├── pyproject.toml
├── Makefile
├── docker-compose.yml
└── CLAUDE.md                 # instruções para Claude Code (ver CLAUDE-implementation.md)
```

---

## 7. Decisões de Implementação

| Decisão | Escolha | Motivo |
|---|---|---|
| Orquestração | LangGraph | Grafo com estado explícito, controle fino, auditabilidade |
| Estado compartilhado | TypedDict | Type safety, sem dependências entre agentes, fácil de serializar |
| LLM padrão | OpenAI GPT-4o-mini (dev), GPT-4o (case study) | Custo vs qualidade |
| Execução de código | exec() com restricted globals | Simples, suficiente para TCC |
| Banco de metadados | SQLite + JSON | Zero config, portátil, suficiente para v0 |
| Package manager | uv | Rápido, moderno, compatível com pyproject.toml |
| Python | 3.11+ | match-case, TypedDict melhorado, performance |

> Ver justificativas completas em `artefact/decisions.md`.

---

## 8. O que NÃO está no escopo v0

- UI web
- Autenticação
- Streaming / CDC
- Cloud deploy
- Connectors: MongoDB, S3, Parquet, Kafka
- Fine-tuning de LLMs
- Execução paralela de múltiplos pipelines
- Join de mais de 2 fontes

> Ver `artefact/overview.md` para os 3 horizontes: TCC v0, produto (v1), pesquisa futura.
