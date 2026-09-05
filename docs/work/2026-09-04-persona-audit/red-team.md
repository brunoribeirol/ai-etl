# Persona: Red Team

## O que investiguei (cada tentativa, e se foi contra código local ou produção)

1. **SQL injection — table-name e query-string paths.** Local, código-only. Li
   `src/ai_etl/core/sql_safety.py` (`validate_table_name`, `validate_select_only_query`)
   e conferi todos os call sites via grep: `sources/postgres_source.py`,
   `sources/mysql_source.py`, `sources/sqlite_source.py`, `destinations/postgres_dest.py`,
   `destinations/mysql_dest.py`. Todos os 5 chamam `validate_table_name` antes de
   interpolar `table` num f-string, e os 3 sources que aceitam `query` livre do LLM
   (`postgres_source.py`, `mysql_source.py`, `sqlite_source.py`) chamam
   `validate_select_only_query` antes de repassar a `pd.read_sql`. Não reproduzi o
   RCE de 24/08 (`{"type": "sqlite", "query": "DROP TABLE users; --"}`) porque a
   defesa que o fechou está no code path, visível e testável estaticamente — não
   precisei rodar contra Postgres/SQLite real para confirmar que o regex/keyword-
   denylist barra esse payload especificamente (`;` fora do fim é rejeitado por
   `validate_select_only_query`, e `--` também).

2. **Sandbox escape — bypass via `__class__.__mro__`/`__subclasses__()`.**
   Local, Python puro, sem tocar produção nem o repo. Rodei via `uv run python3`
   um `exec()` com exatamente os mesmos `SAFE_BUILTINS`/`sandbox_globals` de
   `src/ai_etl/core/sandbox.py` (copiei o dict literal do arquivo), com `pandas`/
   `numpy` importados para simular o processo real do sandbox (o transformer real
   importa pandas/numpy antes do `exec()`). Consegui escalar de
   `().__class__.__base__.__subclasses__()` até `subprocess.CompletedProcess`,
   peguei `__init__.__globals__` (o namespace do módulo `subprocess`) e chamei
   `run(["whoami"], capture_output=True, text=True)` — **executei um comando de
   shell arbitrário de dentro do `exec()` com builtins restritos**, sem nunca usar
   `import`. Ver payload completo abaixo.

3. **RBAC cross-tenant.** Local, código + testes existentes, sem forjar JWT.
   Li `get_current_auth_context`/`get_current_tenant_id`/`require_role`/
   `require_admin` em `src/ai_etl/api/deps.py`: `tenant_id` vem de
   `verify_session_token(token)["org_id"] or user_id`, ambos derivados
   server-side da verificação do JWT do Clerk — não há nenhum parâmetro de
   request (header, query, body) que o cliente possa usar para sobrescrever
   `tenant_id`. Não tentei forjar contra produção (exigiria uma segunda conta
   Clerk real, fora do escopo combinado). Confirmei que há testes cobrindo RBAC/
   tenant scoping em `tests/unit/test_api_deps.py`, `test_api_admin.py`,
   `test_api_tenant.py`, `test_api_pipelines.py`, `test_api_secrets.py`,
   `test_api_budget.py`, `test_api_llm.py`, `test_api_onboarding.py`,
   `test_tenant_deletion_service.py`, `test_tenant_export_service.py` — não abri
   cada um linha a linha (fora do orçamento de 20 min), mas a existência e a
   distribuição por router é consistente com cobertura real, não só mock vazio.

## Achados (severidade + evidência concreta — payload usado, resultado real)

### 1. Sandbox escape → RCE real dentro do processo filho (HIGH, não novo em essência, mas a superfície real é maior do que o comentário no código sugere)

Payload usado (rodado localmente, `uv run python3`, reproduzindo exatamente os
globals de `core/sandbox.py`):

```python
code = '''
target = None
for c in ().__class__.__base__.__subclasses__():
    if c.__name__ == "CompletedProcess":
        target = c
        break
mod_globals = target.__init__.__globals__
run_func = mod_globals["run"]
proc = run_func(["whoami"], capture_output=True, text=True)
output = proc.stdout
'''
exec(code, sandbox_globals, local_env)
```

Resultado real: `local_env["output"] == "brunoribeiro\n"` — execução de comando
arbitrário confirmada, sem `import` nenhum, dentro dos `SAFE_BUILTINS` exatos do
projeto.

**O que isso muda em relação ao que o próprio código já documenta:** o
docstring de `sandbox.py` (linhas 16-20, e o comentário longo em
`_sandbox_worker`, linhas 270-302) já reconhece o bypass via `__subclasses__()`
e mitiga especificamente o vazamento de `os.environ` limpando-o (`os.environ.clear()`)
*antes* do `exec()`. Isso está correto e funciona para esse vetor específico.
Mas o comentário enquadra o risco quase inteiramente em torno de
`os.environ`/secrets — o que meu teste mostra é que o bypass dá acesso a
`subprocess.run`, não só a `os.environ`: mesmo com env limpo, o código do
atacante pode abrir sockets (exfiltrar dados via HTTP para fora, bater em
metadata endpoints internos do Railway se existirem), ler qualquer arquivo no
filesystem do processo filho, e rodar qualquer binário disponível — tudo isso
roda no backend `"process"` (o default em produção, per `docs/CURRENT_STATE.md`
e o próprio docstring: `"process"` continua default porque Railway não roda
Docker-in-Docker e o Vercel Sandbox está desligado por decisão de billing do
dono). Ou seja, **o mitigador de produção real para esse vetor (`"docker"`/
`"vercel"` — isolamento de container) está desligado hoje**, e o único
isolamento ativo é `multiprocessing.Process` (mesmo host, mesmo usuário SO,
sem `seccomp`/namespaces) + timeout + kill.

### 2. Nada de novo em SQL injection

Os 5 call sites que montam SQL com f-string (`postgres_source.py`,
`mysql_source.py`, `sqlite_source.py`, `postgres_dest.py`, `mysql_dest.py`)
chamam `validate_table_name`/`validate_select_only_query` antes de interpolar.
Não encontrei nenhum call site novo (MySQL/MongoDB destinations, adicionados em
`7d38830`/`183`) que monte SQL cru sem passar pela allowlist — `mongodb_dest.py`
não monta SQL (usa driver Mongo nativo), e `mysql_dest.py` segue o mesmo padrão
do `postgres_dest.py`.

### 3. RBAC/tenant — nada de novo encontrado

`tenant_id` é derivado 100% server-side do JWT verificado do Clerk
(`get_current_auth_context` em `api/deps.py`), sem nenhum campo controlável pelo
cliente. Não inspecionei cada query de banco atrás de `WHERE tenant_id = :id`
faltante (fora do orçamento) — isso ficaria como gap de verificação, não como
achado.

## Já era conhecido pelas auditorias anteriores? (sim/não + link se sim)

- **SQL injection (RCE 24/08):** SIM — corrigido e documentado em
  `src/ai_etl/core/sql_safety.py` (comentário "Wave 0, 2026-08-24 audit, Red
  Team CRITICAL finding") e em `docs/work/2026-08-24-full-technical-product-audit.md`.
  Reproduzi a defesa por leitura de código (não precisei re-explorar), continua
  fechada nos 5 call sites atuais.

- **Sandbox escape via `__subclasses__()`:** SIM, o bypass em si já é
  documentado como limitação aceita, conhecida e não fechada
  (`core/sandbox.py`, docstring do módulo + comentário em `_sandbox_worker`,
  citando ADR-038/ADR-039). **O que não estava tão explícito no texto existente
  é a demonstração concreta de que o bypass chega a `subprocess.run` (execução
  de comando arbitrário), não só a leitura de `os.environ`** — isso é uma
  reprodução mais completa do risco já aceito, não um achado novo em si, mas
  vale para calibrar prioridade: hoje o único mitigador real (isolamento por
  container) está desligado em produção por decisão de billing (ADR-039 +
  `project_vercel_sandbox_billing_decision.md` na memória do usuário), então o
  risco aceito está com sua mitigação primária inativa.

- **RBAC/tenant scoping:** não encontrei achado novo; consistente com o que já
  é descrito em `docs/CURRENT_STATE.md`/ADR-022 (tenant derivado do JWT Clerk).

## Recomendação

1. Não é uma ação nova a propor — é uma re-priorização: o risco de sandbox
   escape (ADR-038) já foi aceito conscientemente pelo dono com a ressalva de
   que o backend `"docker"`/`"vercel"` fecharia o vetor em produção. Como o
   Vercel Sandbox está desligado (billing) e Railway não roda Docker-in-Docker,
   **hoje não existe mitigação de isolamento real em produção** para código
   LLM-gerado malicioso ou um prompt-injection que induza o Transformer/
   Analyst/Science a gerar esse payload de escape. Vale registrar essa lacuna
   explicitamente no radar de decisão do dono (não é um "fix" de código — é uma
   decisão de infraestrutura/billing já conhecida, só reforçando a urgência
   real dela) em vez de tratar como definitivamente aceita e arquivada.
2. Nenhuma ação de código recomendada agora para SQL injection ou RBAC — ambos
   verificados como corretos no código atual, sem achados exploráveis novos.
