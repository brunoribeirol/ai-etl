# Persona: SRE

## O que investiguei

1. Estado real da infra agora (não confiando em docs antigos): Railway (projeto
   `proactive-wisdom`, 4 serviços: `ai-etl`, `Redis`, `tranquil-appreciation`,
   `celery-beat`) via `mcp__railway__get-status`/`list-services`, e Vercel
   (projeto `ai-etl`, `prj_55IU6Ntx7CviFT9VN4lNM9Cbs3Jp`) via
   `mcp__plugin_vercel_vercel__list_deployments`/`get_deployment`.
2. `core/llm.py` — o circuit breaker por provider (ADR-041) e sua cobertura de
   teste real (`tests/unit/test_llm.py`), e todos os call sites de `invoke_llm()`.
3. `services/scheduler.py` (Celery beat task `check_scheduled_pipelines_task`) e
   `services/health_alerts.py`/`services/execution_queue.py`
   (`_record_scheduled_pipeline_health_best_effort`) — o caminho real de "3
   falhas seguidas → o que acontece".
4. `docs/CURRENT_STATE.md` linhas ~1-246 (sessões 2026-09-03/04, "Owner's next
   steps", Deploy) e ~1825-1869 ("Known risks / open items") comparado ao
   estado real observado agora.

Não usei as auditorias anteriores (`docs/work/2026-08-24-...md`, artifact
publicado) como fonte de verdade — apenas como candidatos a re-verificar.

## Achados (severidade + evidência concreta)

**Nenhum achado de severidade alta ou média.** Toda infra observada está
saudável agora:

- **Railway**: os 4 serviços (`ai-etl`, `Redis`, `tranquil-appreciation`
  [worker], `celery-beat`) têm `latestDeployment.status == "SUCCESS"`. Os 3
  serviços de app (não-Redis) foram redeployados juntos em
  `2026-09-04T23:22:08Z` (mesmo timestamp — provavelmente um redeploy em lote,
  consistente com os merges recentes #187-#190 vistos no histórico do Vercel).
  Nenhum crashloop, nenhum deploy `FAILED`/`CRASHED` nos dados retornados.
- **Vercel**: os últimos 10 deploys de produção (`target: "production"`) estão
  todos `state: "READY"`, até o commit mais recente na `main`
  (`59ffd5d` — "docs: record owner's next steps at session close (#190)").
  O alias `ai-etl.vercel.app` **aponta corretamente para esse deploy mais
  recente** (`get_deployment("ai-etl.vercel.app")` retorna o mesmo `dpl_...`/
  commit do topo da lista, `aliasError: null`) — o risco documentado
  ("`ai-etl.vercel.app` não segue deploy automaticamente") **não se
  reproduziu agora**, mesmo padrão já registrado na entrada de 2026-09-03
  ("did **not** reproduce this session").
- **Circuit breaker do LLM provider (ADR-041)**: tem cobertura de teste real
  e específica para "provider fora do ar" —
  `tests/unit/test_llm.py::TestInvokeLlmCircuitBreaker` cobre: falha isolada
  não abre o circuito, atingir o threshold abre e falha rápido
  (`LLMCircuitOpenError`), sucesso reseta o contador, o circuito fecha após
  cooldown (half-open probe), e circuitos são independentes por provider.
  Todos os 7 agentes que chamam LLM (`transformer`, `orchestrator`, `advisor`,
  `science`, `planner`, `analyst`, `reviewer`) passam por `invoke_llm()`, não
  por `llm.invoke()` direto — nenhum bypass encontrado.
- **Alerting de pipeline agendado com 3 falhas seguidas**: rastreado no
  código, não assumido pelo nome. `HEALTH_ALERT_FAILURE_THRESHOLD` (default 3,
  `execution_queue.py:113`) é comparado com `==` (não `>=`) contra
  `pipeline["consecutive_failures"]` em
  `_record_scheduled_pipeline_health_best_effort` — dispara
  `check_and_alert_pipeline_health()` exatamente uma vez na 3ª falha
  consecutiva, via os 4 canais configurados (email/Slack/Teams/Google Chat),
  e depois fica silencioso (não reincide a cada falha subsequente — decisão
  documentada no próprio código, ADR-020 Decisão 3). Toda a chamada é
  best-effort (`except Exception: pass`) — uma falha de alerta nunca derruba
  o run em si. Existe teste dedicado exatamente nesse ponto de virada:
  `test_execution_queue.py::test_record_scheduled_pipeline_health_alerts_exactly_at_threshold`.
- **`docs/CURRENT_STATE.md`'s "Known risks / open items" (~linha 1825)**: dos
  2 riscos de infra citados no prompt —
  - Alias do Vercel não seguindo deploy: **ainda listado como risco aberto no
    doc, mas não se manifestou nesta verificação** (mesmo padrão já observado
    e registrado em 2026-09-03). O doc já é honesto sobre isso — não é caso de
    "resolvido silenciosamente sem atualizar o doc", é um risco intermitente
    que segue documentado corretamente como tal.
  - Hang do `alembic upgrade head`: não teria como re-verificar sem rodar uma
    migração real (fora do escopo "não corrija/reinicie nada agora"); o doc já
    marca como não totalmente diagnosticado, sem alegar estar resolvido — nada
    a contestar aqui sem executar a migração.

## Já era conhecido pelas auditorias anteriores? (sim/não + link se sim)

- Estado saudável de Railway/Vercel agora: não é um "achado" per se, é a
  ausência de achados — consistente com a auditoria de 2026-09-03
  (`docs/CURRENT_STATE.md` linha 226) que já relatou tudo funcionando após os
  fixes de performance.
- Circuit breaker com teste real para "provider fora do ar": já implementado
  e testado (ADR-041), não é novidade desta sessão — confirmado, não
  descoberto.
- Alerting de 3 falhas seguidas funcionando como descrito: já documentado
  (Sprint 15, ADR-020) e coberto por teste — confirmado, não descoberto.
- Risco do alias do Vercel: **sim**, já conhecido — `docs/CURRENT_STATE.md`
  linha 1827 ("Known risks"), e a entrada de 2026-09-03 já havia notado que
  não se reproduziu naquela sessão.

## Recomendação

Nenhuma ação corretiva necessária agora — infra real está saudável e os
mecanismos de resiliência (circuit breaker, alerting de pipeline, best-effort
error swallowing) estão implementados e testados como a documentação alega,
não apenas por nome de função.

Único item de baixo custo que vale considerar (não bloqueante): o risco do
alias do Vercel já foi confirmado por duas sessões consecutivas como não
determinístico ("às vezes segue, às vezes não") — se continuar não se
reproduzindo por mais algumas sessões, vale rebaixar a severidade da entrada
no "Known risks" de "recorrente" para "histórico, não observado desde
2026-08-18", para não fazer o leitor achar que é um risco ativo todo deploy.
Isso é uma sugestão de clareza de doc, não uma correção de infra.
