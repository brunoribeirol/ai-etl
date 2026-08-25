# CLAUDE.md — AI-ETL Framework

## O que é este projeto

AI-ETL é um framework multiagente baseado em LLMs para automatizar pipelines ETL end-to-end.
O usuário fornece uma especificação em linguagem natural; 5 agentes especializados orquestrados
via LangGraph executam extração, transformação, qualidade e carga, gerando código Python auditável.

**Contexto:** TCC de Ciência da Computação — CESAR School, 2026.

**Fonte canônica de contexto (decisões, requisitos, pesquisa):**
`~/Documents/Obsidian Vault/tcc/`

---

## Antes de qualquer tarefa — leia nesta ordem

| O que mudou? | O que ler primeiro |
|---|---|
| Um agente | `src/ai_etl/agents/<nome>.py` → vault: `artefact/architecture.md` |
| Estado do pipeline | `src/ai_etl/core/state.py` |
| Topologia do grafo | `src/ai_etl/core/graph.py` |
| Source ou destination | `src/ai_etl/sources/` ou `destinations/` |
| Audit/logging | `src/ai_etl/audit/logger.py` + `src/ai_etl/audit/db.py` |
| Sandbox de execução | `src/ai_etl/core/sandbox.py` + `docs/adr/ADR-003-exec-sandbox.md` |
| Decisão de arquitetura | `docs/adr/` → vault: `artefact/decisions.md` |
| Contexto geral do TCC | vault: `CONTEXT.md` |

**Fonte canônica — vault Obsidian (`~/Documents/Obsidian Vault/tcc/`):**
- `artefact/architecture.md` — spec técnica autoritativa dos 5 agentes
- `artefact/decisions.md` — por que as escolhas foram feitas
- `artefact/requirements.md` — RFs e RNFs por agente
- `artefact/case-study.md` — protocolo dos 3 cenários
- `artefact/security.md` — riscos e mitigações
- `artefact/testing.md` — estratégia de testes
- `CONTEXT.md` — estado geral do TCC e próximos passos

> Os arquivos em `docs/adr/` são a exceção: ADRs ficam no repo porque são decisões de implementação, não documentação de pesquisa.

---

## Comandos disponíveis

```bash
make install       # uv sync --all-extras + instala pacote editável
make test          # pytest unit + integration, cov >80%
make lint          # ruff check
make format        # ruff format + fix
make format-check  # ruff format --check (CI)
make type-check    # mypy src/
make security      # bandit + pip-audit
make check         # tudo acima em sequência
make db-up         # PostgreSQL via docker-compose
make run-scenario1 # executa cenário 1 do estudo de caso
make run-scenario2
make run-scenario3
```

---

## Regras não-negociáveis (violação = revert imediato)

**Nunca faça:**
- f-strings para queries SQL — use SQLAlchemy parâmetros bindados (`text("... WHERE id = :id")`)
- `exec()` fora de `src/ai_etl/core/sandbox.py`
- Commitar `.env` — use apenas `.env.example`
- API keys em logs — o logger já redacta automaticamente campos "key", "token", "secret"
- Quebrar a assinatura dos nodes LangGraph — todo node: `(state: PipelineState) -> PipelineState`
- Mutar o estado in-place — sempre `{**state, "campo": novo_valor}`
- Commitar direto em `main`

**Sempre faça:**
- Usar `log_action()` de `src/ai_etl/audit/logger.py` para cada ação relevante de cada agente
- Type hints em todo código — rodar `make type-check` antes de commitar
- Escrever testes para novos módulos em `tests/unit/` ou `tests/integration/`
- Rodar `make check` antes de abrir qualquer PR

---

## Padrão SR Big Tech — aplicado automaticamente em toda sessão

**Este padrão se aplica a TODO trabalho neste projeto, sem exceção.**
Spec completa: `.claude/specs/sr-standard.md`
Checklist de execução: `.claude/skills/sr-quality-check.md` (invoke via `/sr-quality-check`)

### Antes de marcar qualquer tarefa como concluída

```bash
make check  # lint + format-check + type-check + test + security — deve passar 100%
```

Checklist específico deste projeto:
- Todo node LangGraph: assinatura `(state: PipelineState) -> PipelineState`, retorno `{**state, ...}`
- `log_action()` chamado em toda ação relevante de cada agente
- Short-circuit `if state.get("error"): return state` em todo node
- `exec()` apenas em `core/sandbox.py`
- SQL apenas via `SQLAlchemy text()` com parâmetros — nunca f-string
- `sqlite3.connect()` sempre com `contextlib.closing()`
- Nenhum `print()` em produção — usar `log_action()`
- Testes cobrindo: happy path + short-circuit + audit log + error
- Cobertura ≥ 80% mantida
- Nenhum `# type: ignore` novo sem comentário explicando o motivo

### Anti-patterns críticos deste projeto

| Anti-pattern | Consequência |
|---|---|
| `state["campo"] = valor` | Mutação in-place — quebra o contrato LangGraph |
| `exec()` fora do sandbox | Sem restrição de builtins — risco de segurança |
| `conn = sqlite3.connect(); ...; conn.close()` | Connection leak se exceção ocorrer |
| `f"SELECT * FROM {table}"` direto | SQL injection |
| `query` de `load_postgres()` vindo de input de usuário | SQL injection em SaaS |
| `# type: ignore` sem comentário | Dívida técnica invisível |
| Integration tests que duplicam unit tests | Não testa integração real |

### Convenções Git obrigatórias a partir de agora

- Branch: `feat/<nome>`, `fix/<nome>`, `chore/<nome>`, `docs/<nome>`, `test/<nome>`
- Commits: Conventional Commits em inglês (`feat: ...`, `fix: ...`, `test: ...`)
- Nunca commitar direto em `main`
- Tags: Semantic Versioning `vMAJOR.MINOR.PATCH` a cada milestone

---

## Skills disponíveis

- `.claude/skills/add-agent.md` — checklist completo para adicionar um novo agente LangGraph
- `.claude/skills/run-pipeline.md` — como rodar e verificar um cenário do case study
- `.claude/skills/sr-quality-check.md` — auditoria SR Big Tech antes de qualquer entrega

---

## Arquitetura em uma frase

```
[spec]                                              (Silver — pipeline ETL, grafo LangGraph)
  → Orchestrator (LLM, plano JSON)
  → Extractor (determinístico, CSV/PG/REST/... → DataFrame + schema)
  → Transformer (LLM → código Python → sandbox exec → DataFrame)
  → Quality (determinístico, nulls + duplicates + outliers → severity)
  → Loader (determinístico, DataFrame → CSV/PG/S3)
     └─ se severity == "error" → END (pipeline bloqueado)

[pergunta de negócio]                     (Agentic BI — camada de análise, fora do grafo)
  → Planner (LLM, decompõe a pergunta em sub-análises descritivas/analíticas)
  → Analyst/Science (LLM → código → sandbox, uma chamada por sub-análise
    + auto-repair; Reviewer faz segunda passada opt-in por resultado — ADR-037)
  → Advisor (LLM, sintetiza Gold/Science em recomendações prescritivas)
```

Todo estado compartilhado via `PipelineState` TypedDict em `src/ai_etl/core/state.py`.
Toda ação registrada via `log_action()` → persistida em JSON + SQLite por `save_run()`.
Orquestração ponta a ponta (Silver → Planner → Analyst/Science → Advisor) em
`src/ai_etl/services/pipeline_service.py::run_full_analysis`.

---

## Stack

```
Python 3.11+  |  langgraph>=0.2  |  langchain>=0.3  |  langchain-openai>=0.3  |  openai>=1.50
pandas>=2.0   |  sqlalchemy>=2.0 |  httpx>=0.27     |  python-dotenv
```

Dev: `ruff` | `mypy` (strict, 3.12) | `bandit` | `pip-audit` | `pytest-cov` | `pre-commit`

---

## Variáveis de ambiente

```bash
OPENAI_API_KEY=sk-...
AI_ETL_LLM_MODEL=gpt-4o-mini   # gpt-4o para case study final
POSTGRES_URL=postgresql://ai_etl:ai_etl@localhost:5432/ai_etl_db
```

---

## Estrutura de pastas

```
src/ai_etl/
├── agents/
│   ├── pipeline/    # orchestrator, extractor, transformer, quality, loader (Silver, grafo LangGraph)
│   └── analysis/    # planner, analyst, science, advisor, reviewer (Agentic BI, fora do grafo)
├── api/             # FastAPI: main.py, deps.py, config.py, serialization.py
│   └── routers/     # pipelines, runs, admin, budget, cost_estimation, llm, onboarding, secrets, tenant
├── services/        # camada de orquestração: pipeline_service.py (run_full_analysis),
│                     # execution_queue, scheduler, auth/secrets/tenant services, alerting, digest
├── core/            # state.py, graph.py, sandbox.py, llm.py, pricing.py, drift.py, scheduling.py, ...
├── sources/         # csv, postgres, mysql, mongodb, rest, sqlite, document
├── destinations/    # csv_dest, postgres_dest, s3_parquet_dest
└── audit/           # logger.py, models.py, storage.py, connection.py, admin_log.py
    └── db/          # budget, health, locale, onboarding, pipelines, retention, runs

tests/
├── unit/           # sem I/O externo — mocker para LLM e fontes
├── integration/    # agentes com mocks de LLM, fontes reais
└── e2e/            # 3 cenários completos

docs/
├── architecture.md
├── adr/            # ADR-001 a ADR-004 (e futuros)
└── case-study.md

case_study/
├── pipelines/      # scenario1_spec.txt, scenario2_spec.txt, scenario3_spec.txt
├── data/           # datasets (gitignored)
└── results/        # JSONs dos runs (scenario1/, 2/, 3/)
```
