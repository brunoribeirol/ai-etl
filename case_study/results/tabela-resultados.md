# Tabela de Resultados — Estudo de Caso AI-ETL

**Modelo:** `gpt-4o-mini`  
**Data:** 2026-06-23  
**Total de runs:** 15 (5 por cenário)

---

## Resultados por Run

| Cenário | Run | Status | Tempo (s) | Quality | Linhas Carregadas | Tentativas LLM |
|---------|-----|--------|-----------|---------|-------------------|----------------|
| 1 | 1 | completed | 9.2 | warning | 3 649 | 2 |
| 1 | 2 | completed | 8.7 | warning | 3 649 | 2 |
| 1 | 3 | completed | 10.7 | warning | 3 649 | 2 |
| 1 | 4 | completed | 13.2 | warning | 3 649 | 2 |
| 1 | 5 | completed | 11.2 | warning | 3 649 | 2 |
| 2 | 1 | completed | 14.3 | warning | 7 135 | 2 |
| 2 | 2 | completed | 17.2 | warning | 7 135 | 2 |
| 2 | 3 | completed | 15.9 | warning | 7 135 | 2 |
| 2 | 4 | completed | 17.8 | warning | 7 135 | 2 |
| 2 | 5 | completed | 15.3 | warning | 7 135 | 2 |
| 3 | 1 | completed | 20.6 | warning | 7 510 | 2 |
| 3 | 2 | completed | 26.7 | warning | 7 510 | 2 |
| 3 | 3 | completed | 24.8 | warning | 7 510 | 2 |
| 3 | 4 | completed | 26.4 | warning | 7 510 | 2 |
| 3 | 5 | completed | 23.9 | warning | 7 510 | 2 |

---

## Resumo por Cenário

| Cenário | Taxa de Sucesso | Tempo Médio (s) | Tentativas LLM (média) | Linhas Carregadas |
|---------|----------------|-----------------|------------------------|-------------------|
| 1 — CSV simples | 5/5 (100%) | 10.6 | 2.0 | 3 649 |
| 2 — CSV + PostgreSQL | 5/5 (100%) | 16.1 | 2.0 | 7 135 |
| 3 — CSV + PostgreSQL + REST | 5/5 (100%) | 24.5 | 2.0 | 7 510 |

---

## Observações

- **Quality severity `warning` em todos os runs:** esperado — os datasets injetam nulos (~5%) e outliers propositalmente para testar o Quality Agent. Nenhum run foi bloqueado (erro ≥ 20% de nulos).
- **2 tentativas LLM em todos os runs:** o Transformer sempre usou 2 tentativas (a spec é suficientemente complexa para o modelo gerar código que precisa de um refinamento). Nunca excedeu o limite de 3.
- **Tempo cresce linearmente com complexidade:** Cenário 3 (~24.5s) é 2.3× mais lento que Cenário 1 (~10.6s), refletindo o custo de extração de 3 fontes + código de transformação mais elaborado.
- **Reprodutibilidade perfeita:** todos os runs do mesmo cenário produziram exatamente o mesmo número de linhas carregadas (determinístico no dataset).

---

## Bugs descobertos e corrigidos durante execução

| Bug | Impacto | Fix |
|-----|---------|-----|
| `all`, `any` não disponíveis no sandbox | `failed` em run com código LLM usando `all()` | Adicionados a `SAFE_BUILTINS` em `core/sandbox.py` |
| Quality-blocked pipeline mantinha `status="running"` | Runs bloqueados por quality ficavam com status incorreto | `quality_node` seta `status="failed"` quando `severity=="error"` |
| `rest_source` não normalizava estrutura time-series | `weather_df` chegava como 1 linha com listas nas células | Detecção de sub-dict com listas paralelas adicionada a `rest_source.py` |
| Spec ambígua no Cenário 3 | LLM tentava join por data (2024 vs 2026) → 100% nulls | Spec atualizada para especificar enriquecimento por cidade |

---

## Limitações desta rodada e protocolo de re-execução (auditoria M10, 2026-09-09)

Registrado após a auditoria de 12 personas (`docs/work/2026-09-08-full-persona-audit.md`,
persona Academic Evaluator) para manter a narrativa do TCC honesta sobre o que esta tabela
cobre e o que **não** cobre:

- **Modelo único e datado.** Todos os 15 runs usaram `gpt-4o-mini` em **2026-06-23**. O
  produto hoje expõe 4 modelos em 2 provedores (OpenAI + Anthropic). Não há, neste artefato,
  uma comparação multi-modelo para os cenários do estudo de caso (a comparação de estabilidade
  existente vive em `case_study/results/sprint8/`, com outro escopo).
- **3 de 4 cenários.** O README anuncia 4 cenários (CSV; +Postgres; +REST; +PDF/DOCX). Esta
  tabela cobre os cenários 1–3 (15 runs = 5×3). O cenário 4 (documento) e o cenário 5
  (sqlite+rest+auth, presente em `tests/e2e/`) **não** têm resultados de estudo de caso
  commitados.
- **Não reproduzível só a partir do repositório.** `case_study/data/` é gitignored, então uma
  banca não consegue reproduzir os números só clonando o repo — precisa dos datasets. A
  engenharia é reproduzível (testes, migrações); a *avaliação* não é, sem os dados.
- **Artefatos por run não commitados.** Só o agregado (esta tabela + `runs_log.csv`) está no
  repo; os JSONs individuais de cada run (cenários 2/3) são `.gitkeep`-only.

### Protocolo de re-execução (para fechar as lacunas acima)

1. Repor os datasets em `case_study/data/` (mesmos seeds; ver `case_study/scripts/`).
2. Rodar `make run-scenario1|2|3` (+ os specs de cenário 4/5) com o modelo default e, para a
   comparação, com um modelo Anthropic — registrar `--output case_study/results/scenarioN/`.
3. Commitar os JSONs por run + regenerar esta tabela e `runs_log.csv`; datar a nova rodada.
4. Anotar explicitamente, de novo, o resultado `warning` (esperado: nulos/outliers injetados),
   para que a banca não o leia como falha.

Bloqueado nesta sessão por falta dos datasets (gitignored) e por exigir chamadas LLM reais —
é uma tarefa agendável pelo dono, não um item de código.
