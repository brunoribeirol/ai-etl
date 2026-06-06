# CLAUDE.md — AI-ETL Framework

## O que é este projeto

AI-ETL é um framework de sistemas multiagentes baseados em LLMs para automatizar pipelines ETL end-to-end. O usuário fornece uma especificação em linguagem natural; 5 agentes especializados orquestrados via LangGraph executam as etapas de extração, transformação, qualidade e carga de dados, gerando código Python auditável.

**Contexto:** TCC de Ciência da Computação — CESAR School, 2026. Não é um produto em produção.

**Todo o conhecimento do projeto está em:** `~/Documents/Obsidian Vault/tcc/`

Esse vault Obsidian é a fonte canônica de contexto, decisões, pesquisa bibliográfica, requisitos e documentação técnica. O código neste repositório é o artefato — o vault é o cérebro.

---

## Leia antes de qualquer tarefa de código

**Neste repositório:**
1. `src/ai_etl/core/state.py` — PipelineState TypedDict (contrato central de todos os agentes)
2. `src/ai_etl/core/graph.py` — topologia do grafo LangGraph
3. O agente relevante em `src/ai_etl/agents/`
4. `docs/architecture.md` — specs completas (prompts, fluxo, erros de cada agente)

**No vault (contexto completo):**
- `~/Documents/Obsidian Vault/tcc/artefact/architecture.md` — spec técnica autoritativa
- `~/Documents/Obsidian Vault/tcc/artefact/decisions.md` — por que as escolhas foram feitas
- `~/Documents/Obsidian Vault/tcc/artefact/requirements.md` — RFs e RNFs por agente
- `~/Documents/Obsidian Vault/tcc/artefact/case-study.md` — protocolo dos 3 cenários
- `~/Documents/Obsidian Vault/tcc/artefact/security.md` — riscos e mitigações
- `~/Documents/Obsidian Vault/tcc/artefact/testing.md` — estratégia de testes
- `~/Documents/Obsidian Vault/tcc/CONTEXT.md` — estado geral do TCC e próximos passos

---

## Arquitetura em uma frase

**5 agentes LangGraph** (Orchestrator → Extractor → Transformer → Quality → Loader) compartilham um `PipelineState` TypedDict. Cada agente lê do estado, executa, e retorna o estado atualizado. O Audit module registra todas as ações.

---

## Regras obrigatórias

**Nunca faça:**
- f-strings para queries SQL — use SQLAlchemy parâmetros bindados (`text("... WHERE id = :id")`)
- `exec()` fora de `src/ai_etl/core/sandbox.py`
- Commitar `.env` — use apenas `.env.example`
- API keys em logs — o logger já redact automaticamente campos "key", "token", "secret"
- Quebrar a assinatura dos nodes LangGraph — todo node: `(state: PipelineState) -> PipelineState`

**Sempre faça:**
- Usar `log_action()` de `src/ai_etl/audit/logger.py` para cada ação relevante de cada agente
- Retornar `{**state, "campo": novo_valor}` — nunca mutar o estado in-place
- Type hints em todo código — rodar `make type-check` antes de commitar
- Escrever testes para novos módulos em `tests/unit/` ou `tests/integration/`

---

## Stack

```
Python 3.11+  |  langgraph>=0.2  |  langchain>=0.3  |  openai>=1.50
pandas>=2.0   |  sqlalchemy>=2.0 |  httpx>=0.27     |  python-dotenv
```

---

## Comandos

```bash
make install       # uv sync --all-extras
make test          # pytest unit + integration
make test-e2e      # pytest e2e (requer Docker)
make check         # lint + type-check + test
make db-up         # PostgreSQL via docker-compose
make run-scenario1 # executa cenário 1 do estudo de caso
```

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
├── agents/      # um arquivo por agente LangGraph node
├── core/        # state.py, graph.py, sandbox.py, llm.py
├── sources/     # csv_source, postgres_source, rest_source
├── destinations/# csv_dest, postgres_dest
└── audit/       # logger.py, db.py

tests/
├── unit/        # sandbox, state, logger — sem I/O externo
├── integration/ # agentes com mocks de LLM e fontes reais
└── e2e/         # 3 cenários completos

case_study/
├── pipelines/   # scenario1_spec.txt, scenario2_spec.txt, scenario3_spec.txt
├── data/        # datasets (gitignored)
└── results/     # JSONs dos runs (scenario1/, scenario2/, scenario3/)
```
