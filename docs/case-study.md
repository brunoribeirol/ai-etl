---
title: AI-ETL — Estudo de Caso
type: project
tags: [tcc, artefato, estudo-de-caso, avaliacao]
project: tcc-ai-etl
created: 2026-06-05
updated: 2026-06-05
status: active
---

# AI-ETL — Estudo de Caso

> Este documento define os cenários de avaliação, a baseline de comparação e o protocolo de execução do estudo de caso do TCC.

---

## Dataset

### Opções candidatas (escolher com orientador)

| Dataset | Fonte | Tamanho | Por quê |
|---|---|---|---|
| NYC Taxi Trip Data | NYC Open Data (público) | 1M+ linhas/mês | Real, documentado, múltiplas colunas, problemas de qualidade naturais |
| AdventureWorks | Microsoft (sintético) | ~10-50K linhas | Banco relacional completo, bem documentado, educacional |
| Open-Meteo API | open-meteo.com (público, gratuito) | JSON paginado | REST API pública, sem autenticação, confiável |
| Faker (sintético) | Python library | Customizável | Controle total sobre problemas de qualidade injetados |

**Recomendação:** usar NYC Taxi (CSV) + AdventureWorks subset (PostgreSQL) + Open-Meteo API (REST). Todos públicos, zero custo, bem documentados. Problemas de qualidade podem ser injetados programaticamente no NYC Taxi antes da execução.

### Preparação dos dados
- Anonymização: não necessária (dados públicos)
- Problemas de qualidade a injetar no CSV de vendas:
  - 5% de valores nulos aleatórios em 3 colunas
  - 2% de linhas duplicadas
  - 10% de valores de data em formato errado (dd/mm vs mm/dd)
  - 1% de outliers (valores 10x fora do IQR)

---

## Cenários de Avaliação

### Cenário 1 — Pipeline Simples (CSV → CSV)

**Objetivo:** validar extração, transformação básica e carga no caso mais simples possível.

**Spec em linguagem natural:**
> "Leia o arquivo vendas.csv. Renomeie a coluna 'dt_venda' para 'data_venda', converta para tipo data. Filtre apenas registros com valor_total maior que 0. Remova duplicatas. Salve como vendas_limpo.csv."

**Fontes:** 1 arquivo CSV (5.000 linhas, 8 colunas)
**Destino:** CSV
**Agentes envolvidos:** Orchestrator, Extractor (csv), Transformer, Quality, Loader, Audit
**Transformações:** rename, type cast, filter, dedup
**Problemas de qualidade injetados:** nulos em valor_total, 2% duplicatas

**Métricas:**
- Pipeline executado sem intervenção? (sim/não)
- Nº de tentativas de geração de código
- Quality report detectou duplicatas e nulos? (precision/recall)
- Tempo de execução (s)
- Linhas no output vs esperado

**Critério de sucesso:** 4/5 runs concluídos sem intervenção humana

---

### Cenário 2 — Pipeline Médio (CSV + PostgreSQL → PostgreSQL)

**Objetivo:** validar join entre fontes heterogêneas, limpeza mais complexa e carga em banco relacional.

**Spec em linguagem natural:**
> "Leia o arquivo vendas.csv e a tabela clientes do PostgreSQL. Faça um join pelo campo customer_id. Mantenha apenas registros onde o join foi bem-sucedido. Converta o campo data_venda para o formato YYYY-MM-DD. Calcule o ticket médio por cliente. Salve o resultado na tabela resumo_clientes do PostgreSQL."

**Fontes:**
- CSV: vendas (10.000 linhas)
- PostgreSQL: tabela clientes (5.000 linhas)

**Destino:** PostgreSQL (tabela resumo_clientes)
**Agentes envolvidos:** todos os 5 + Audit
**Transformações:** join (2 fontes), type cast, filter (inner join only), aggregate (avg por group)
**Problemas de qualidade:** customer_ids não presentes em clientes (~15% de vendas órfãs), datas em formato misto

**Métricas:** mesmas do Cenário 1 + contagem de linhas antes/depois do join

**Critério de sucesso:** 4/5 runs concluídos sem intervenção; join produz resultado correto

---

### Cenário 3 — Pipeline Complexo (CSV + PostgreSQL + REST API → PostgreSQL)

**Objetivo:** validar o caso mais completo do framework: 3 fontes heterogêneas, enriquecimento via API, pipeline ponta a ponta.

**Spec em linguagem natural:**
> "Leia o arquivo vendas.csv, a tabela clientes do PostgreSQL e busque dados climáticos do dia de cada venda na Open-Meteo API para a cidade de Recife. Faça um join de vendas com clientes pelo customer_id e enriqueça com a temperatura máxima do dia da venda. Remova registros com dados climáticos faltando. Salve o resultado enriquecido na tabela vendas_clima do PostgreSQL."

**Fontes:**
- CSV: vendas (10.000 linhas)
- PostgreSQL: clientes (5.000 linhas)
- REST API: Open-Meteo (dados climáticos por data e coordenada)

**Destino:** PostgreSQL (tabela vendas_clima)
**Agentes envolvidos:** todos os 5 + Audit
**Transformações:** 2 joins, enriquecimento com API, filter, type cast
**Desafios extras:** paginação da API, rate limiting, merge de 3 fontes

**Métricas:** mesmas anteriores + nº de chamadas à API, tempo de execução

**Critério de sucesso:** 3/5 runs concluídos sem intervenção (aceita-se maior taxa de falha por complexidade)

---

## Baseline de Comparação

### O que é o baseline

Para que "mínima intervenção humana" seja mensurável, cada cenário é também implementado manualmente em pandas pelo próprio autor, servindo de comparação direta.

### Como construir o baseline

Para cada cenário:
1. Implementar o mesmo pipeline manualmente em um Jupyter Notebook (pandas + psycopg2 + requests)
2. Registrar:
   - Tempo total de implementação (minutos)
   - Número de linhas de código escritas
   - Número de decisões explícitas necessárias (ex: "qual coluna usar no join?")
   - Número de erros durante o desenvolvimento (tracebacks)
   - Número de iterações até o resultado correto

### Tabela comparativa esperada

| Métrica | Baseline (manual) | AI-ETL | Diferença |
|---|---|---|---|
| Tempo de implementação | X min | Y min | (X-Y)/X % |
| Linhas de código escritas | X linhas | ~0 (gerado) | — |
| Decisões explícitas necessárias | X | Y (intervenções) | — |
| Erros durante desenvolvimento | X tracebacks | Y falhas de agente | — |

### Nota metodológica
O baseline não invalida o AI-ETL se houver empate em alguns cenários simples. O ponto do TCC não é "AI-ETL é sempre melhor", mas "AI-ETL automatiza o processo com auditabilidade preservada". A análise deve ser honesta sobre casos onde o overhead do sistema (latência LLM, múltiplas tentativas) não compensa.

---

## Protocolo de Múltiplas Execuções

### Por que múltiplas execuções

LLMs são não-determinísticos: a mesma spec pode gerar código diferente, ou falhar em algumas execuções. Para que os resultados do TCC sejam defensáveis, cada cenário deve ser executado múltiplas vezes.

### Protocolo

- **Número de runs por cenário:** 5 runs independentes
- **Condições:** mesma spec, mesmo dataset, sem modificação manual entre runs
- **Registro obrigatório por run:** número do run, timestamp, spec usada, código gerado (salvo em arquivo), quality report, resultado (sucesso/falha), tipo de falha (se houver), nº de tentativas de geração, tempo de execução

### Classificação dos resultados

| Resultado | Critério |
|---|---|
| Sucesso completo | Pipeline executou do início ao fim sem intervenção humana |
| Sucesso parcial | Pipeline executou mas Quality bloqueou o Load (comportamento esperado, não falha) |
| Falha recuperável | Transformer falhou nas 3 tentativas; pipeline abortou com log claro |
| Falha não recuperável | Exceção não tratada; pipeline abortou sem log útil |

### Tabela de resultados esperada (por cenário)

| Run | Resultado | Tentativas Transformer | Tempo (s) | Quality bloqueou? | Intervenção? |
|---|---|---|---|---|---|
| 1 | ? | ? | ? | ? | ? |
| 2 | ? | ? | ? | ? | ? |
| 3 | ? | ? | ? | ? | ? |
| 4 | ? | ? | ? | ? | ? |
| 5 | ? | ? | ? | ? | ? |
| **Taxa de sucesso** | X/5 | média: Y | média: Zs | — | — |

### Análise qualitativa

Para cada tipo de falha encontrada, registrar:
- Qual foi a spec que gerou a falha?
- Qual foi o erro do código gerado?
- O traceback capturado foi útil para entender o problema?
- O Quality Agent identificou o problema antes da carga?
- Seria possível corrigir o problema ajustando a spec? (auditabilidade humana)

---

## Cronograma do Estudo de Caso

| Atividade | Mês | Semana |
|---|---|---|
| Preparação do dataset (injeção de erros) | Set/26 | 1 |
| Mock da REST API (Flask local) | Set/26 | 1 |
| Execução Cenário 1 (5 runs) + baseline | Set/26 | 2 |
| Execução Cenário 2 (5 runs) + baseline | Set/26 | 3 |
| Execução Cenário 3 (5 runs) + baseline | Set/26 | 4 |
| Consolidação dos resultados | Out/26 | 1 |
| Análise qualitativa + escrita da Seção 4 (resultados) | Out/26 | 2-3 |
