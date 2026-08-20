# SR Big Tech Standard — AI-ETL

Spec canônica do padrão de qualidade aplicado a este projeto.
**Última revisão: 2026-06-22**

---

## O que este padrão cobre

Este documento define o que "pronto" significa neste projeto no nível de um Senior/Staff Engineer
de Big Tech, considerando o contexto específico: TCC 2026 → SaaS futuro, Python, LLMs, pipelines
de dados, arquitetura multiagente com LangGraph.

---

## 1. Arquitetura

### Contratos inegociáveis
- Todo node LangGraph: `(state: PipelineState) -> PipelineState`
- Estado imutável: `return {**state, "campo": valor}` — nunca `state["campo"] = valor`
- Todo agente chama `log_action()` para cada ação relevante
- Todo agente faz short-circuit em `if state.get("error"): return state`
- `exec()` SOMENTE em `src/ai_etl/core/sandbox.py`
- SQL SOMENTE via SQLAlchemy `text()` com parâmetros bindados

### Separação de camadas
```
agents/         ← lógica de domínio (o que o pipeline FAZ)
core/           ← infraestrutura de execução (como o pipeline FUNCIONA)
sources/        ← I/O de entrada (adaptadores)
destinations/   ← I/O de saída (adaptadores)
audit/          ← observabilidade (cross-cutting concern)
```

Nenhuma camada importa de uma camada "acima" dela. Agentes importam de `core/` e `audit/`.
`core/` não importa de `agents/`. Sources e destinations não importam entre si.

### Extensibilidade (sem over-engineering)
- Novo agente → seguir `.claude/skills/add-agent.md` exatamente
- Nova source/destination → seguir o padrão dos existentes (csv_source como referência)
- Decisão de arquitetura que afeta estrutura → criar ADR antes de implementar

---

## 2. Testes

### Pirâmide
```
         /e2e\        ← 3 cenários completos do case study (execução real end-to-end)
        /------\
       /integr  \     ← agentes com LLM mockado, fontes/destinos reais em memória
      /----------\
     /  unit tests \  ← funções isoladas, mocks para LLM e I/O externo (maioria dos testes)
    /--------------\
```

### Cobertura mínima
- Lógica de negócio (agentes, quality checks): ≥ 80%
- Adaptadores (sources, destinations): ≥ 70%
- Cobertura de `main` e `__init__.py` excluída (configurado no pyproject.toml)

### O que todo agente deve ter em `tests/unit/`
1. Happy path — estado transformado corretamente
2. Short-circuit — upstream error retorna estado sem alteração
3. Audit log — entrada de `log_action` é adicionada
4. Error handling — agente seta `state["error"]` e `status = "failed"` em falha

### O que os integration tests devem testar
Integração real entre componentes — NÃO duplicar unit tests.
Exemplos válidos: pipeline parcial com dados reais, múltiplos agentes encadeados com LLM mock.

---

## 3. Tipagem

- `mypy --strict` deve passar sem erros em `src/`
- Todo `# type: ignore` deve ter comentário explicando por quê
- Sem `Any` desnecessário onde o tipo real é conhecido
- Funções internas (`_nome`) podem ter tipos mais relaxados, mas públicas nunca

### Padrão atual de ignores aceitos
```python
df: pd.DataFrame = state["transformed_data"]  # type: ignore[assignment]
# → non-None guaranteed by error short-circuit above
```

---

## 4. Segurança

### Regras absolutas
- `.env` nunca commitado (`.gitignore` já cobre)
- API keys nunca em logs (logger redacta automaticamente)
- SQL: `text("... WHERE id = :id", {"id": value})` — nunca f-string
- `exec()`: apenas em `sandbox.py` com `SAFE_GLOBALS` restrito
- **Toda migração que cria uma tabela nova (`op.create_table`) deve ligar Row Level Security nela na mesma migração** (`op.execute("ALTER TABLE ... ENABLE ROW LEVEL SECURITY")`) — o Supabase concede CRUD completo pros papéis `anon`/`authenticated` em toda tabela nova por padrão, mesmo esse projeto nunca usando o SDK/API REST do Supabase. Achado real e corrigido em produção em 20/08/2026 (ver `SECURITY.md` e vault `bugs-solved/supabase-rls-disabled-anon-authenticated-full-crud.md`) — não repita esse gap em migrações futuras.

### Limitações documentadas e aceitas para TCC
- Sandbox `exec()` pode ser bypassed via introspection (`().__class__.__mro__[1].__subclasses__()`)
  → Documentado em `docs/adr/ADR-003-exec-sandbox.md` e `SECURITY.md`
  → Para SaaS: Docker/gVisor (ver SaaS Roadmap abaixo)
- `timeout_seconds` no sandbox não é enforced (v0 limitation)
  → Para SaaS: `multiprocessing` com `Process.terminate()` ou container timeout

### Checagem de dependências
```bash
make security  # bandit + pip-audit
```
Rodar a cada nova dependência adicionada.

---

## 5. Observabilidade

### Logging
- Nenhum `print()` em código de produção
- Toda ação relevante via `log_action()` de `audit/logger.py`
- Runs persistidos em JSON + SQLite via `save_run()` de `audit/db.py`
- `sqlite3.connect()` SEMPRE com `contextlib.closing()` para garantir `close()`

### Tratamento de erros
- Erros fatais: setar `state["error"]` e `state["status"] = "failed"`
- Erros de I/O: capturar, logar, propagar via state
- Nunca silenciar exceções com `except Exception: pass`

---

## 6. Git e Fluxo

### Convenções obrigatórias
- Branch: `feat/<nome>`, `fix/<nome>`, `chore/<nome>`, `docs/<nome>`, `test/<nome>`
- Commits: Conventional Commits em inglês
  ```
  feat: add schema validation to extractor
  fix: close sqlite connection on exception
  test: add integration tests for transformer retry logic
  docs: add ADR-005 for sandbox timeout strategy
  chore: bump langgraph to 0.3.0
  ```
- Tags: Semantic Versioning `v1.0.0`, `v1.1.0`, `v1.1.1`
- PRs: nunca direto em `main`, mesmo trabalhando sozinho

### CHANGELOG
Manter `CHANGELOG.md` atualizado a cada tag usando o formato Keep a Changelog.
Template: `## [Unreleased]` → mover para `## [vX.Y.Z] - YYYY-MM-DD` ao taggear.

---

## 7. Documentação

### O que documentar e onde
| Tipo | Onde |
|---|---|
| Decisão de arquitetura com trade-offs | `docs/adr/ADR-NNN-<nome>.md` |
| Setup, uso, cenários | `README.md` |
| Módulo/agente público | Docstring no arquivo |
| Lógica não-óbvia | Comentário inline no código |
| Contexto do TCC | `~/Documents/Obsidian Vault/tcc/` |

### ADR Numbering
Próximo ADR disponível: ADR-020 (atualizado 20/08/2026 — ADR-005 a ADR-019 já existem, ver `docs/adr/`; ADR-017 = Sprint 17, ADR-018 = Sprint 14, ADR-019 = Sprint 29 (`feat/sprint29-tenant-budget-cap`), todas em PRs abertos, não mergeados — merge order definida como 17 → 14 → 29; confirmar que não colide com sprints em paralelo antes de reusar o número).
Verificar `docs/adr/` antes de criar.

---

## 8. SaaS Roadmap — pontos de atenção futuros

**Atualizado 20/08/2026** — a maioria dos itens originais desta tabela já foi implementada (Sprints 1-4, 6, 8, 9, 10, 11, 12, 23). Fonte de verdade agora é o roadmap unificado de 29 sprints no Vault: `~/Documents/Obsidian Vault/tcc/artefact/sprint-roadmap.md` (1-11) + `product-roadmap-post-tcc.md` (12-29). Esta seção fica só como referência histórica de quais decisões já viraram ADR.

| Área | Estado atual (20/08/2026) | ADR |
|---|---|---|
| Multi-tenancy | ✅ Feito — `tenant_id` real via Clerk, isolamento por tenant | ADR-006 |
| Armazenamento de runs | ✅ Feito — `StorageBackend` local/S3, prefixado por tenant | ADR-009 |
| Sandbox | ✅ Unificado (`multiprocessing.Process`, timeout real, timeout escalado por tamanho de dado) — Docker/gVisor não avaliado ainda | ADR-007, ADR-013 |
| Autenticação | ✅ Feito — Clerk JWT via middleware | ADR-006, ADR-011 |
| Rate limiting | ✅ Feito — fixed-window por tenant no Redis | ADR-008 |
| Configuração | .env fixo — config service (SSM/Vault) ainda não avaliado | — (candidato: Sprint 19/24 do roadmap de produto) |
| Billing | Custo por execução rastreado; billing real (Stripe) ainda não implementado | ADR-008 (custo) — billing é Sprint v1.0 do roadmap de produto |
| Isolamento de dados | ✅ Feito — tenant_id em audit log e storage | ADR-006, ADR-009 |
| Escala (datasets grandes) | ✅ Feito — perfilado e corrigido contra 204k linhas × 300 colunas (schema cap + timeout dinâmico) | ADR-013 |
| Diversidade de fontes | ✅ Feito — CSV/Postgres/REST(+auth+OAuth2)/Document/SQLite/MySQL/MongoDB | ADR-010, ADR-012 |
| Multi-cloud | IaC AWS drafted (Terraform, ECS Fargate), não aplicado — prova de portabilidade, não migração | ADR-015 |
| Robustez a dado sujo do mundo real | Não testado além do estudo de caso sintético | Sprint 22 do roadmap de produto |
| Fallback multi-provedor de LLM | ✅ Suporte a Anthropic/Google/Ollama além de OpenAI (seleção via config, sem failover automático ainda) | ADR-014 |
| Compliance formal (SOC2/LGPD/GDPR) | Nenhum | Sprint 24 do roadmap de produto |

---

## 9. Checklist de entrega (resumo executivo)

```
[ ] make check passa sem erros
[ ] Contrato LangGraph respeitado em todos os agentes tocados
[ ] Nenhum secret hardcoded, nenhum print() em produção
[ ] Testes escritos: happy path + short-circuit + audit log + error
[ ] Cobertura ≥ 80% mantida
[ ] Nenhum # type: ignore novo sem comentário
[ ] Documentação atualizada (README, ADR, ou docstring conforme o caso)
[ ] Branch com nome semântico, commit com Conventional Commits
[ ] sqlite3 com contextlib.closing(), SQL com SQLAlchemy text()
```
