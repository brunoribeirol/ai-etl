# Persona: Tech Lead

## O que investiguei

- `docs/CURRENT_STATE.md` (topo do arquivo + seções `2026-09-03`/`2026-09-04`, sessões 1-4) e o Artifact "AI-ETL Platform Audit" (`b859d4b5-...`, atualizado 2026-09-04) para saber o que já foi checado e não re-confirmar às cegas.
- Rodei de verdade (não confiei em "make check clean" relatado):
  - `uv run ruff check src/ tests/`
  - `uv run ruff format --check src/ tests/`
  - `uv run mypy src/`
  - `uv run bandit -c pyproject.toml -r src/ai_etl/`
  - `uv run pip-audit` (sem o `pip install --upgrade pip` que o Makefile roda antes, de propósito, para ver o achado cru)
- `grep -rn "# type: ignore"` e `grep -rniE "TODO|FIXME|XXX|HACK"` em `src/ai_etl/`.
- Tamanho de arquivo (`wc -l`) dos módulos de agente e de serviço mais centrais (`pipeline_service.py`, `execution_queue.py`, agentes de `pipeline/` e `analysis/`).
- Drift ADR↔código em 2 pontos concretos: backend de sandbox default (ADR-038/039 vs. `core/sandbox.py`) e levantamento de todas as seções "Follow-up"/"Known limitation"/"Future work" em `docs/adr/*.md`, para ver se alguma promete algo que o código não cumpre.
- `docker ps` (containers de teste já estavam de pé, não precisei subir nada).

Não rodei a suíte de testes completa (`make test`) nem `make test-e2e` — consumiriam a maior parte do orçamento de tempo, e a sessão 4 do `CURRENT_STATE.md` já documenta números recentes de cobertura (95.3%, 1168 testes) obtidos rodando de verdade; foco desta persona foi lint/type/security estáticos + drift arquitetural, que são os que uma auditoria anterior pode mais facilmente "alegar limpo" sem ter rodado.

## Achados

### 1. `# type: ignore` sem comentário explicativo em `api/serialization.py:43` — severidade BAIXA

```
src/ai_etl/api/serialization.py:43:    return fig.to_plotly_json()  # type: ignore[no-any-return]
```

O `CURRENT_STATE.md` (sessão 3, 2026-09-04) afirma explicitamente: **"Commented every remaining unexplained `# type: ignore`"**, listando os 5 de `core/graph.py` e os 3 `@celery_app.task`. Esse de `serialization.py` não está nessa lista e continua sem comentário explicando o motivo do ignore (diferente dos outros 9 ocorrências no repo, todas comentadas). O código ao redor tem um comentário de bloco, mas ele explica o *formato* do retorno (payload do Plotly.js), não *por que* o mypy precisa do ignore (`Figure.to_plotly_json()` não tem stub tipado, retorna `Any`).

Violação direta da própria regra do projeto ("No new `# type: ignore` without a comment explaining why") — não é crítico (é auto-explicável para quem conhece Plotly), mas é o tipo exato de item que a sessão 3 disse ter fechado 100% e não fechou.

### 2. `pip` desatualizado é reportado por `pip-audit` isolado — severidade INFORMATIVA, não é bug novo

```
Name Version ID              Fix Versions
---- ------- --------------- ------------
pip  26.1.2  PYSEC-2026-3721 26.2
```

Confirmei que isso é esperado: `make security` roda `uv run pip install --upgrade pip` imediatamente antes do `pip-audit` no Makefile, então o pipeline real do projeto não sofre esse achado. Só aparece se alguém rodar `pip-audit` isolado (como fiz de propósito, sem o upgrade antes) — já documentado e "explicado" no `CURRENT_STATE.md` sessão 3 ("Correction, not a fix").

### 3. `services/pipeline_service.py` com 1015 linhas / 17 funções top-level — severidade BAIXA, observação

Maior módulo do projeto por uma margem grande (o segundo maior, `execution_queue.py`, tem 651 linhas). Não é um "god function" — é uma "god file" com funções coesas (`run_silver_pipeline`, `run_gold_analysis`, `run_science_analysis`, `run_full_analysis`, `resume_pending_load`, `reject_pending_load`, etc.), todas girando em torno de orquestração de execução de pipeline. Funcionalmente correto e nenhuma função individual parece anormalmente grande, mas é o candidato mais óbvio a split (`pipeline_service/silver.py`, `.../gold_science.py`, `.../approvals.py`) se o projeto continuar crescendo. Não bloqueante.

### 4. Restante de `ruff/mypy/bandit`: limpo de verdade

- `ruff check src/ tests/`: **All checks passed!**
- `ruff format --check src/ tests/`: **192 files already formatted** (o `ruff format --check` sem escopo pega 5 arquivos de `case_study/` fora do alvo do Makefile — não é um achado, é escopo do comando errado da minha parte na primeira tentativa).
- `mypy src/`: **Success: no issues found in 96 source files**.
- `bandit -c pyproject.toml -r src/ai_etl/`: **0 issues** (Medium/High/Low todos zero), 29 `#nosec` supressões documentadas, nenhuma nova.
- Nenhum `TODO`/`FIXME`/`XXX`/`HACK` esquecido em `src/ai_etl/`.

### 5. ADRs com seções "Follow-up"/"Known limitation": nenhuma promessa quebrada silenciosamente

Levantei todas as ocorrências (`ADR-012`, `ADR-014`, `ADR-018`, `ADR-032`, `ADR-033`, `ADR-036`, `ADR-040`, `ADR-042`, `ADR-043`, `ADR-044`, `ADR-045`). Todas as que verifiquei estão marcadas explicitamente como não resolvidas *no próprio texto* (ex.: ADR-042 pede verificação manual do PITR do Supabase fora do código; ADR-036 documenta que valores de data/moeda em DataFrames não são reformatados por locale, só o texto narrativo do LLM). Não achei nenhum ADR afirmando algo como concluído que o código contradiga — o padrão do projeto de "flagged, not silently dropped" parece real, não só retórica.

Drift pontual verificado (ADR-038/039 — sandbox backend default): `core/sandbox.py:382` resolve o backend via `AI_ETL_SANDBOX_BACKEND` env var, default `"process"` — bate exatamente com o que `CURRENT_STATE.md` e o ADR afirmam (Vercel Sandbox Pro recusado, backend "process" seguindo default).

## Já era conhecido pelas auditorias anteriores? (sim/não + link)

- Achado 1 (`type: ignore` não comentado em `serialization.py`) — **NÃO** estava no Artifact "AI-ETL Platform Audit" (`https://claude.ai/code/artifact/b859d4b5-89a4-4479-b372-af0b5c0ab62a`) nem no `CURRENT_STATE.md`; a sessão 3 alegou ter fechado "every remaining unexplained type: ignore" e esse ficou de fora. É um achado novo, ainda que de severidade baixa.
- Achado 2 (pip desatualizado via `pip-audit` isolado) — **SIM**, já documentado em `docs/CURRENT_STATE.md` (seção "2026-09-04 (session 3)", parágrafo "Correction, not a fix").
- Achado 3 (tamanho de `pipeline_service.py`) — não encontrei menção explícita em nenhuma auditoria anterior nem ADR; não é claramente "novo" no sentido de bug, é uma observação estrutural que ninguém parece ter formalizado antes.
- Achado 4 (lint/type/security limpos) — consistente com o que `CURRENT_STATE.md` já reivindicava; confirmei rodando de verdade em vez de aceitar a alegação.
- Achado 5 (drift ADR↔código) — nenhum drift real encontrado; consistente com o que as auditorias anteriores já vinham reportando (nenhuma contradição nova).

## Recomendação

1. Comentar o `# type: ignore[no-any-return]` em `api/serialization.py:43` com uma linha curta (ex.: `# plotly's Figure.to_plotly_json() has no typed stub, returns Any`) — 1 minuto de trabalho, fecha de verdade a alegação da sessão 3 de "every remaining unexplained type: ignore" comentado.
2. Não tratar `pipeline_service.py` como incêndio — é candidato a split arquitetural (Silver vs. Gold/Science vs. approvals) só se o módulo continuar crescendo; registrar como item de observação para a próxima vez que alguém for adicionar uma função grande ali, não abrir tarefa agora.
3. Nada além disso: `ruff`/`mypy`/`bandit` genuinamente limpos, sem `TODO`/`FIXME` esquecidos, sem drift ADR↔código nos pontos verificados. A alegação de "prototype funcionalmente pronto" do `CURRENT_STATE.md` se sustenta na dimensão de qualidade de código estática que esta persona cobriu — a ressalva relevante é a mesma que o próprio doc já registra: cobertura estática não substitui rodar o produto de verdade (foi assim que a sessão 2 achou os 2 bugs reais que a auditoria de código sozinha não pegou).
