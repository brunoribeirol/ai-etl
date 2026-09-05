# Persona: QA

## O que investiguei (números reais)

Rodei a suíte de verdade (não confiei em relatos anteriores), containers de teste já
estavam de pé (`ai-etl-app-postgres-test-1`, `ai-etl-postgres-test-1`, `ai-etl-redis-1`,
todos "Up" há 20-23h).

**Passo 1 — unit + integration** (2026-09-04 20:28-20:30, ~119s):
```
1168 passed, 13 skipped, 1 warning
TOTAL coverage: 95% (4030 stmts, 190 miss)
```
Os 13 skips são reais e esperados, não escondidos: `test_mongodb_source_real.py` (3),
`test_mysql_source_real.py` (3), `test_sandbox_vercel.py` (7) — integrações contra infra
real (MongoDB/MySQL live, Vercel Sandbox) que este ambiente não tem credenciais/acesso
para exercitar (Vercel Sandbox está desabilitado no plano Hobby, decisão já registrada
em memória do projeto). `test_sandbox_docker.py` (8 testes) **rodou de verdade**, não
skipou — a imagem `ai-etl-sandbox:latest` está buildada localmente.

**Passo 2 — e2e** (rodado isolado do passo 1, conforme instrução — bug de isolamento
Celery eager-mode já documentado, não é achado novo), 18.53s:
```
5 passed (scenario1_csv, scenario2_csv_postgres, scenario3_csv_postgres_rest,
scenario4_document, scenario5_sqlite_rest_auth)
```

**Total real**: 1173 passed, 13 skipped (justificados), 0 falhas, 0 erros. Cobertura 95%.

Áreas com cobertura mais baixa (reais, não escondidas no report): `sandbox_vercel.py`
44% (backend não exercitável sem infra Vercel), `sandbox.py` 60% (paths de escalonamento
SIGKILL, historicamente marcados como "untestable by design" pela auditoria anterior),
`sandbox_docker.py` 83%, `loader.py` 84%.

## Achados

### 1. `tests/integration/test_quality.py` não testa integração nenhuma — duplica `tests/unit/test_quality.py` (severidade: baixa/média)

Evidência: `tests/integration/test_quality.py` tem 4 testes
(`test_clean_dataframe_has_ok_severity`, `test_high_null_ratio_produces_error`,
`test_duplicates_produce_warning`, `test_error_state_is_passed_through`) que chamam
`quality_node()` diretamente com um DataFrame in-memory — zero mocks, zero DB, zero
I/O externo (`grep -c "Mock\|mocker\|patch("` retornou 0 no arquivo). É uma função pura
determinística, exatamente a mesma forma de teste que os unitários.

`tests/unit/test_quality.py` já cobre os mesmos três cenários com nomes quase idênticos:
`test_quality_node_ok_severity_for_clean_data`, `test_quality_node_error_severity_for_high_nulls`,
`test_quality_node_warning_severity_for_duplicates` (linhas 47, 60, 92) — mesma função,
mesmo tipo de asserção, nenhuma infraestrutura real testada em nenhum dos dois lugares.
O arquivo em `tests/integration/` deveria testar algo que exercita I/O real (como
`test_audit_persistence.py` e `test_tenant_isolation_rls.py` do mesmo diretório fazem
corretamente, contra Postgres real com skip automático se não alcançável) — não duplicar
um teste unitário sob um rótulo diferente.

Contraste positivo: os outros arquivos em `tests/integration/` são integração de verdade
— `test_audit_persistence.py` e `test_tenant_isolation_rls.py` rodam migrations Alembic
reais contra Postgres live e testam RLS/isolamento de tenant real; `test_sandbox_docker.py`
sobe um container Docker real; `test_alembic_migration.py` roda migrations reais.
`test_quality.py` é a exceção nesse diretório.

### 2. Nenhum padrão sistemático de asserções fracas encontrado

Busquei `assert .* is not None$` (89 ocorrências em 20 arquivos) e `assert True` (0
ocorrências) para checar se a cobertura de 95% esconde testes vazios. Inspecionei uma
amostra (`test_transformer.py:67,148`, `test_advisor.py:263`, `test_science.py:112,246`)
— em todos os casos o `is not None` acompanha asserções de conteúdo mais fortes na mesma
função de teste (ex.: `test_science.py:109-112` também checa `isinstance(...DataFrame)`
e `not .empty` antes do `fig is not None`), ou é o resultado correto de um cenário de
falha proposital (`result["error"] is not None` depois de forçar um LLM a devolver código
inválido 3x). Não é um padrão de "teste vazio disfarçado de cobertura".

### 3. Item do punch-list anterior confirmado corrigido

A auditoria de 2026-08-24 registrava "Alta — fix the 2 known-broken integration tests" e
"`sources/postgres_source.py::load_postgres` has zero test coverage" (38%). Confirmado
hoje: todos os testes de integração passam (0 falhas) e `postgres_source.py` está em
100% de cobertura agora. Não é um achado novo — é confirmação de que a correção
registrada realmente aconteceu (não apenas na documentação).

## Já era conhecido pelas auditorias anteriores? (sim/não + link)

- **Achado 1 (test_quality.py duplicado)**: **não** — a auditoria de 2026-08-24
  (`docs/work/2026-08-24-full-technical-product-audit.md:219`) fez uma "mock-density
  analysis" geral e concluiu que a suíte é majoritariamente não-superficial, mas não
  aponta esse arquivo específico como duplicação de unit↔integration. É um achado novo,
  porém de severidade baixa (não é um problema de segurança nem de honestidade da
  suíte — só um rótulo de diretório mal aplicado a 4 testes).
- **Achado 2 (ausência de padrão de asserção fraca)**: consistente com o que a auditoria
  de 2026-08-24 já havia concluído ("<10% da suíte é pure-mechanics assertion") —
  confirmação, não achado novo.
- **Achado 3 (punch-list corrigido)**: era um item aberto conhecido; confirmo que foi
  de fato resolvido, rodando os testes de verdade (não apenas lendo `CURRENT_STATE.md`).

## Recomendação

- Baixa prioridade, cosmético: mover os 4 testes de `tests/integration/test_quality.py`
  para `tests/unit/test_quality.py` (ou deletá-los por redundância) e, se houver
  intenção real de ter um teste de integração para o agente Quality, escrevê-lo contra
  algo que de fato varie com infraestrutura real (ex.: um `pipeline_plan` com
  `quality_checks` vindos de um plano gerado de verdade, ou encadeado depois do
  Transformer real). Não é urgente — não esconde bug nem infla cobertura de forma
  enganosa (a mesma lógica já está coberta em `tests/unit/`).
- Nenhuma ação necessária quanto à honestidade geral da suíte: os números batem com o
  que a documentação relata, os skips são justificados e documentados no próprio código
  (não escondidos), e não há evidência de asserções vazias infladas artificialmente a
  cobertura.
