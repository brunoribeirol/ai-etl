# Persona: Blue Team

## O que investiguei

1. **Logging/auditoria**: leitura de `src/ai_etl/audit/logger.py`; execução real (não só leitura)
   de `log_action()` via `.venv/bin/python3` com `PYTHONPATH=src`, passando um dict com
   `api_key`, `headers.Authorization`, `nested.password` e um campo neutro (`rows`), para
   confirmar a redação de fato no output. Grep de `print(` em todo `src/ai_etl/` excluindo
   comentários. Contagem de chamadas `log_action(` em cada `agents/pipeline/*.py` e
   `agents/analysis/*.py`.
2. **Redação de secrets**: leitura de `src/ai_etl/services/secrets_service.py` e
   `src/ai_etl/api/routers/secrets.py` completo (57 linhas) — todas as rotas.
3. **RBAC boundary**: leitura de `require_role()`/`require_admin()` em `api/deps.py` e grep de
   todo decorator `@router.(post|patch|put|delete)` em `api/routers/*.py`, verificando se o
   parâmetro seguinte tem `Depends(require_role(...))` ou `Depends(require_admin)`.

## Achados (severidade + evidência concreta)

### 1. Redação de secrets aninhados — FUNCIONA (confirmado por execução real)

Rodei `log_action()` de verdade:

```python
log_action(state, "extractor", "connected_to_source", {
    "api_key": "sk-supersecret123",
    "headers": {"Authorization": "Bearer abcxyz", "X-Custom": "ok"},
    "nested": {"password": "hunter2", "note": "fine"},
    "rows": 42,
})
```

Output real:

```json
"details": {
  "api_key": "***REDACTED***",
  "headers": {"Authorization": "***REDACTED***", "X-Custom": "ok"},
  "nested": {"password": "***REDACTED***", "note": "fine"},
  "rows": 42
}
```

Redação recursiva funciona em profundidade arbitrária (dict aninhado dentro de dict), inclusive
para `Authorization`/`Bearer`, e campos não sensíveis (`X-Custom`, `note`, `rows`) passam intactos.
Severidade: **informativo** (não é achado negativo — é verificação positiva de uma correção
anterior).

### 2. `log_action()` ausente na camada Agentic BI (Planner/Analyst/Science/Advisor/Reviewer) — JÁ É DESIGN CONHECIDO E DOCUMENTADO, NÃO É GAP

Contagem de `log_action(` por arquivo:
- `agents/pipeline/{extractor,loader,orchestrator,quality,transformer}.py`: 3/4/3/2/3 — todos > 0.
- `agents/analysis/{advisor,analyst,reviewer,science}.py`: **0** cada. `planner.py`: 1.

Investigado a fundo antes de reportar como gap: `services/pipeline_service.py` linha 279-298
(`_log_llm_override_if_used`) documenta explicitamente por quê — a camada Agentic BI roda fora do
grafo LangGraph/`PipelineState` (arquitetura documentada no `CLAUDE.md`: "Agentic BI — analysis
layer, outside the graph"), então não tem acesso a `state["audit_log"]` para chamar `log_action`.
O substituto é `logging.warning`/`logging.info` padrão (ex.: `run_gold_analysis` loga falhas de
sub-tarefa via `logger.warning`). Não é um buraco silencioso — é uma decisão de arquitetura
justificada por ADR-031 §5.

### 3. `print()` em produção — apenas `__main__.py` (CLI entrypoint), já conhecido e aceito

```
src/ai_etl/__main__.py:29:  print(f"[ai-etl] Starting pipeline run {run_id}")
src/ai_etl/__main__.py:35:  print(f"[ai-etl] Pipeline {status}. Audit log: {json_path}")
src/ai_etl/__main__.py:38:  print(f"[ai-etl] Error: {final_state['error']}")
```

Nenhum outro `print(` real em `src/ai_etl/` (grep completo, sem falso-positivo em comentário).

### 4. Endpoint de secrets nunca retorna valor decriptado — CONFIRMADO

`api/routers/secrets.py` (arquivo lido por inteiro): `GET /secrets` → `list_secret_names()`
(retorna apenas `list[str]` de nomes); `POST /secrets` e `DELETE /secrets/{name}` não retornam
valor algum. `secrets_service.get_secret()` (que decripta) não é importado nem chamado em
nenhuma rota do router — só existe para consumo futuro server-side por conectores de fonte
(comentário confirma: ADR-022 Decision 4).

### 5. RBAC boundary — todas as rotas mutantes (POST/PATCH/PUT/DELETE) que persistem dado de
tenant têm `Depends(require_role(...))` ou `Depends(require_admin)`. Duas exceções revisadas e
não são gap:

- `POST /runs/estimate` (`cost_estimation.py`) — só `Depends(get_current_tenant_id)`, sem
  `require_role`. Não persiste nada (docstring do módulo: "never touches the tenant's actual
  data or source"; é um cálculo puro).
- `POST /llm/test-connectivity` (`llm.py`) — mesmo padrão, também documentado no docstring:
  "Tenant-scoped auth... even though the test doesn't touch tenant data".

Ambos são ações somente-leitura/sem efeito colateral persistente disfarçadas de POST (corpo de
request, não mutação de estado); qualquer `viewer` autenticado pode chamá-las, mas nenhuma delas
grava, altera ou expõe dado de outro tenant. Não há rota que escreva dado (pipeline, run, secret,
tenant config, budget) sem `require_role`/`require_admin`.

## Já era conhecido pelas auditorias anteriores? (sim/não + link)

- **Achado #1** (redação recursiva funcionando): **sim, indiretamente** —
  `docs/work/2026-08-24-full-technical-product-audit.md` linha 196 registrou o problema ORIGINAL
  (na época, `_sanitize` não recursava e não pegava `authorization`/`Bearer`). Esta auditoria
  confirma, por execução real, que o fix já foi aplicado e funciona corretamente hoje — não é um
  achado novo, é a verificação de que uma correção anterior é real e não apenas cosmética.
- **Achado #3** (`print()` em `__main__.py`): **sim** — mesmo arquivo, linha 152: "defensible as
  CLI UX, but a strict reviewer would flag it", já classificado como Baixa.
- **Achados #2, #4, #5**: não encontrei menção explícita nas auditorias anteriores lidas
  (`2026-08-24-full-technical-product-audit.md`); nada de novo relevante foi descoberto — são
  confirmações de que os controles estão corretos, não gaps.

## Recomendação

Nenhuma ação corretiva necessária nesta rodada. Os três focos de investigação (logging/redação,
redação de secrets, RBAC) estão sólidos e, onde havia um gap real documentado por auditoria
anterior (`_sanitize` não recursivo), a correção já foi aplicada e foi verificada aqui por
execução real, não só leitura de código. Sugestão de baixa prioridade, não bloqueante: mover os
três `print()` de `__main__.py` para `log_action`-equivalente ou `logging` padrão, por
consistência de estilo — mas isso já está no punch-list "Baixa" da auditoria de 2026-08-24 e não
representa risco de segurança (CLI local, sem PII/segredo no output).
