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
