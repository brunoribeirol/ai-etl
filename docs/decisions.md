---
title: AI-ETL — Log de Decisões
type: project
tags: [tcc, artefato, decisoes, arquitetura, tech-stack]
project: tcc-ai-etl
created: 2026-06-05
updated: 2026-06-05
status: active
---

# AI-ETL — Log de Decisões

Registro de decisões de design, arquitetura e tech stack tomadas durante o desenvolvimento.

Formato: **[DATA] Decisão — Razão — Alternativas consideradas — Impacto futuro**

---

## Decisões de Pesquisa

**[Abr 2026] Artigos organizados em core (10) e supporting (12)**
- Core: trabalhos mais diretamente relacionados ao AI-ETL (aparecem nos Trabalhos Relacionados)
- Supporting: contexto e antecedentes (embasam a Fundamentação Teórica)
- Alternativas: categoria única — descartada por perder granularidade na Seção 2 vs Seção 3
- Impacto futuro: separação facilita reuso dos fichamentos em artigo ou dissertação

**[Abr 2026] CSVs de busca bibliográfica não guardados no vault**
- Conteúdo já processado nas fichas individuais
- Vault foca em conhecimento processado, não em dados brutos de busca

**[Jun 2026] Metodologia: Bibliográfica + Estudo de Caso (com DSR como referencial)**
- A classificação formal segue o framework da disciplina (GIL, 2008; YIN, 2015)
- DSR (Hevner et al., 2004) é mencionado como o paradigma que legitima a produção de artefatos como contribuição científica
- Alternativa descartada: usar DSR como classificação principal — o professor não inclui DSR na lista de procedimentos técnicos aceitos, gerando risco de avaliação
- Impacto futuro: para mestrado, DSR pode ser o framework metodológico central com ciclos formalmente documentados

---

## Decisões de Arquitetura

**[Jun 2026] Padrão Orchestrator + Agentes Especializados**
- O Orchestrator Agent centraliza o planejamento e a coordenação; os agentes especializados (Extractor, Transformer, Quality, Loader) executam funções específicas
- Baseado no padrão documentado em DeepPrep (FAN et al., 2026) e MODP (DARSHAN, 2026)
- Alternativa descartada: agente monolítico único — viola RNF01 (modularidade) e dificulta testes unitários
- Alternativa descartada: agentes totalmente autônomos sem orquestrador — impede controle do fluxo de execução e auditabilidade
- Impacto futuro: cada agente pode evoluir independentemente; o Transformer pode ser substituído por um agente mais sofisticado com tree-based reasoning sem afetar os outros

**[Jun 2026] Estado do pipeline como TypedDict compartilhado (LangGraph StateGraph)**
- Um único objeto `PipelineState` é passado entre todos os nós do grafo e incrementalmente populado
- Permite que qualquer agente acesse o resultado dos anteriores e que o log capture o estado completo ao final
- Alternativa descartada: passagem de mensagens ponto a ponto — dificulta o log de auditoria e o rastreamento do estado global
- Impacto futuro: o PipelineState é o embrião do "data lineage" de um produto — cada campo pode gerar uma entrada rastreável no audit trail

**[Jun 2026] Módulo de auditoria como preocupação transversal, não como agente**
- O logger escreve no estado do pipeline e persiste JSON + SQLite fora do fluxo dos agentes
- RF06 (registrar log de todas as ações) é responsabilidade do módulo `audit/`, invocado por todos os nós do grafo
- Alternativa descartada: Audit Agent — adicionaria latência e complexidade sem benefício para o v0
- Impacto futuro: o módulo de auditoria evolui para um serviço de lineage com visualização em UI

**[Jun 2026] Roteamento condicional: Quality bloqueia Load se severity=error**
- O Quality Agent produz um relatório com severidade por check
- Se qualquer check retornar `severity=error`, o Orchestrator interrompe o pipeline antes do Load
- O log registra a razão do bloqueio e o quality report completo
- Alternativa descartada: continuar sempre e registrar erros apenas no log — risco de carregar dados corrompidos no destino

---

## Decisões de Tech Stack

**[Jun 2026] LangGraph (não CrewAI)**
- LangGraph expõe o grafo de execução como StateGraph com nós e arestas condicionais explícitas
- Permite controle total sobre quando cada agente é invocado, com qual estado, e para onde vai
- Facilita documentação acadêmica: o grafo é um diagrama de estados mapeável para o TCC
- CrewAI foi descartado: abstração demais, o "crew" decide a ordem internamente, reduz auditabilidade
- Custom foi descartado: requer implementação de gerenciamento de estado do zero, sem benefício para o prazo
- Impacto futuro: LangGraph suporta persistência de estado, checkpointing e streaming — base para produto

**[Jun 2026] OpenAI GPT-4o-mini para desenvolvimento; GPT-4o para caso de estudo final**
- GPT-4o-mini: $0.15/1M input — 33x mais barato que GPT-4o ($5/1M)
- Para desenvolvimento e testes, GPT-4o-mini é suficiente para 80%+ dos casos
- GPT-4o reservado para os runs finais do caso de estudo acadêmico (precisão máxima)
- Alternativa zero-custo: Ollama + Qwen2.5-Coder-7B (local, sem custo de API)
- Alternativa zero-custo: Gemini 1.5 Flash (1M tokens/min grátis no free tier)
- A troca de provider é trivial: 1 linha em `core/llm.py`
- Impacto futuro: para SaaS, o provider se torna configurável por cliente

**[Jun 2026] Python exec() sandboxado como mecanismo de execução de código**
- Para o TCC: exec() com globals restritos é suficiente para uma demo controlada
- **LIMITAÇÃO CONHECIDA**: exec() não é um sandbox real — bypass via introspeção de objetos é documentado. Ver `artefact/security.md` para detalhes
- Alternativa descartada para v0: Docker por execução — adiciona 2-3s de latência e complexidade operacional desnecessária para o TCC
- **DECISÃO PARA PRODUTO**: substituir exec() por Docker container com `network=none` e filesystem somente leitura antes de qualquer uso em produção

**[Jun 2026] SQLite + JSON como mecanismo de auditoria e histórico**
- JSON por run: arquivo autocontido com toda a informação da execução (reprodutibilidade)
- SQLite: histórico de runs consultável sem dependência de servidor
- Alternativa descartada: PostgreSQL para o log — dependência desnecessária para o v0
- Impacto futuro: migrar o histórico de SQLite para PostgreSQL ou para um banco de lineage (OpenLineage)

**[Jun 2026] uv + pyproject.toml como gerenciador de dependências**
- uv é ~10-100x mais rápido que pip; suporte nativo a pyproject.toml
- pyproject.toml é o padrão moderno do ecossistema Python (PEP 517/518)
- Garante reprodutibilidade do ambiente de desenvolvimento
- Impacto futuro: facilita empacotamento como biblioteca ou publicação no PyPI

**[Jun 2026] pytest como framework de testes**
- Padrão do ecossistema Python moderno; suporte a fixtures, parametrize, mocks
- Ver `artefact/testing.md` para a estratégia completa de testes
- Impacto futuro: base para CI/CD e cobertura mínima obrigatória em PRs

---

## Decisões de Segurança

**[Jun 2026] API keys via .env e nunca comitadas**
- `.env` no `.gitignore` desde o primeiro commit
- `.env.example` com placeholder documentado no repositório
- Impacto futuro: migrar para secrets manager (AWS Secrets Manager, Vault) para produção

**[Jun 2026] Queries SQLAlchemy com parâmetros bindados**
- O Extractor Agent nunca constrói queries com f-string a partir de input do usuário
- Todas as queries usam `text()` com `bindparams` ou a API ORM do SQLAlchemy
- Previne SQL injection via spec em linguagem natural

**[Jun 2026] Dados do caso de estudo: públicos ou sintéticos**
- Nenhum dado sensível ou pessoal é usado no estudo de caso do TCC
- Datasets candidatos: NYC Taxi (público), dados sintéticos gerados por Faker
- Ver `artefact/case-study.md` para o protocolo de preparação de dados

---

## Decisões Pendentes (validar com orientador)

| Decisão | Status | Bloqueador |
|---|---|---|
| Dataset exato do caso de estudo | ⏳ Pendente | Reunião com orientador |
| Visibilidade do repositório GitHub (público vs privado) | ⏳ Pendente | Decisão do aluno |
| Custo da API OpenAI é aceitável para o projeto? | ⏳ Pendente | Alinhamento com orientador |
| REST API do caso de estudo: pública real ou mock local? | ⏳ Pendente | Decisão do aluno |
