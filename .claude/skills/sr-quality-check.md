# Skill: sr-quality-check

Auditoria SR Big Tech para o projeto AI-ETL.
**Rodar antes de marcar qualquer tarefa como concluída.**

---

## Protocolo de execução

### Passo 1 — Rodar make check

```bash
make check
```

Isso executa em sequência: `lint → format-check → type-check → test → security`.
**Se falhar em qualquer etapa: parar e corrigir antes de continuar.**

Reportar cada etapa:
- `make lint` → ruff check (estilo + imports)
- `make format-check` → ruff format --check (formatação)
- `make type-check` → mypy strict (tipagem)
- `make test` → pytest cov ≥80% (cobertura)
- `make security` → bandit + pip-audit (vulnerabilidades)

---

### Passo 2 — Contrato dos agentes LangGraph

Para cada agente novo ou modificado em `src/ai_etl/agents/`, verificar:

| Critério | Como verificar |
|---|---|
| Assinatura `(state: PipelineState) -> PipelineState` | grep no arquivo |
| Retorno `{**state, ...}` — nunca mutação in-place | revisar o return |
| `log_action()` chamado para toda ação relevante | revisar o corpo |
| Short-circuit em `state.get("error")` | verificar primeira linha do node |
| Sem `exec()` fora de `core/sandbox.py` | grep no arquivo |

---

### Passo 3 — Verificar segurança

| Critério | Como verificar |
|---|---|
| Zero secrets hardcoded | grep -r "sk-\|password=\|api_key=" src/ |
| Nenhum print() em código de produção | grep -rn "^\s*print(" src/ |
| SQL: parametrizado via SQLAlchemy text() | grep f"SELECT em src/ |
| sqlite3: sempre com contextlib.closing() | grep sqlite3.connect src/ |
| query param de load_postgres() nunca vem de input externo | revisar chamadas |

---

### Passo 4 — Verificar testes

| Critério | Como verificar |
|---|---|
| Novo módulo tem testes em `tests/unit/` | ls tests/unit/ |
| Happy path coberto | revisar test file |
| Short-circuit (upstream error) coberto | revisar test file |
| Audit log entry coberto | revisar test file |
| Cobertura ≥ 80% no report | ver output do pytest |

---

### Passo 5 — Verificar tipagem nova

Para qualquer código novo:
- Sem `# type: ignore` novos sem comentário explicando o motivo
- Sem `Any` desnecessário onde o tipo real é conhecido
- Sem parâmetros sem tipo em funções públicas

```bash
make type-check 2>&1 | grep -E "error:|note:"
```

---

### Passo 6 — Verificar documentação

| O que mudou | O que atualizar |
|---|---|
| Novo agente | vault: `artefact/architecture.md` + diagrama no README |
| Decisão de arquitetura | `docs/adr/ADR-NNN-<nome>.md` |
| Setup ou configuração | `README.md` |
| Novo source/destination | docstring + exemplo no README |
| State fields adicionados | comentário em `core/state.py` |

---

### Passo 7 — Git (antes de commitar)

- Branch: `feat/<nome>`, `fix/<nome>`, `chore/<nome>`, `docs/<nome>`, `test/<nome>`
- Commit message: Conventional Commits em inglês
  - `feat: add outlier check to quality agent`
  - `fix: close sqlite connection on exception in audit/db.py`
  - `test: add unit tests for extractor schema extraction`
- Nunca commitar em `main` diretamente

---

### Relatório final

Ao terminar, emitir:

```
SR Quality Check — <data>
─────────────────────────────────────
make check:      PASS | FAIL
Contrato LangGraph: PASS | FAIL | N/A
Segurança:       PASS | FAIL
Testes:          PASS | FAIL
Tipagem:         PASS | FAIL
Documentação:    PASS | FAIL | N/A
Git:             PASS | FAIL | N/A
─────────────────────────────────────
STATUS: PRONTO / BLOQUEADO

Bloqueadores (se houver):
- <item específico>
```

Só marcar como PRONTO se todos os critérios obrigatórios passarem.
