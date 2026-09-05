# Persona: LLM/Prompt Engineer

## O que investiguei

- Prompts dos agentes: `src/ai_etl/agents/pipeline/{orchestrator,transformer}.py`,
  `src/ai_etl/agents/analysis/{planner,analyst,science,advisor,reviewer}.py`.
- O mecanismo de segunda passada (ADR-037, `reviewer.py`) e seu gate de ativação
  (`core/llm.py::is_llm_review_enabled`, `AI_ETL_LLM_REVIEW_ENABLED`).
- O guard-rail determinístico de consistência narrativa em `science.py`
  (`_validate_narrative_consistency`).
- Rodei **2 análises reais** em produção (`https://ai-etl.vercel.app/app`, modelo
  `gpt-4o-mini`) com datasets pequenos/propositalmente difíceis:
  1. `tiny_trend.csv` (3 linhas, `month,region,revenue_brl`) — pergunta direcional vaga
     pedindo "conclusão confiante para o board": run
     `543b21f4-d96b-4d65-a17b-af2b0f73ab82`.
  2. `misleading_column.csv` (8 linhas, coluna `profit_margin` com valores monetários
     brutos por cliente, não uma margem calculada) — pergunta de negócio pedindo
     recomendação de "empurrar" clientes para um plano: run
     `71a2750a-7354-4d98-9d6a-41bcd153a688`.
- Reproduzi o comportamento do parser de datas localmente com `pandas==2.3.3` para
  confirmar a causa raiz (não apenas lido o prompt — rodado de verdade, como pedido).

## Achados

### 1. CRÍTICO — o bug de corrupção de datas "corrigido" em 2026-09-04 (sessão 2) ainda reproduz ao vivo, para o caso mais comum

**Não é o mesmo achado do dia 04/09** (aquele foi about `2026-01-02..05`, já fechado).
Este é uma **regressão do mesmo bug, em um subcaso que a própria correção não cobre**,
reproduzida ao vivo duas vezes, em dois runs independentes, hoje:

- Run 1 (`tiny_trend.csv`): entrada `2026-01-01, 2026-02-01, 2026-03-01` → Silver saiu
  como `2026-01-01, 2026-01-02, 2026-01-03` (mês e dia trocados nas linhas 2 e 3).
- Run 2 (`misleading_column.csv`): entrada `2026-01-01/02-01/03-01` (múltiplas linhas)
  → mesmo padrão: `2026-02-01`→`2026-01-02`, `2026-03-01`→`2026-01-03`.

Código gerado pelo Transformer em ambos os runs (idêntico, prompt determinístico):
```python
default_parsed = pd.to_datetime(df['month'], errors='coerce')
dayfirst_parsed = pd.to_datetime(df['month'], errors='coerce', dayfirst=True)
both_ok = default_parsed.notna() & dayfirst_parsed.notna()
disagree = bool(both_ok.any()) and bool((default_parsed[both_ok] != dayfirst_parsed[both_ok]).any())
parsed = (dayfirst_parsed if dayfirst_parsed.isna().sum() <= default_parsed.isna().sum() else default_parsed) if disagree else ...
```
Reprodução local confirma a causa raiz — `dayfirst=True` do pandas **troca dia/mês em
strings ISO `YYYY-MM-DD` sempre que ambos os componentes são ≤ 12**, mesmo a string
sendo inequívoca por formato (ISO nunca é day-first):
```
default:  2026-01-01, 2026-02-01, 2026-03-01
dayfirst: 2026-01-01, 2026-01-02, 2026-01-03   # errado
disagree: True
```
Como as duas leituras têm 0 `NaT` (empate), o critério de desempate do "fix" de sessão 2
(`dayfirst_parsed.isna().sum() <= default_parsed.isna().sum()`) escolhe `dayfirst_parsed`
— a leitura errada — sempre que há empate, **exatamente a mesma falha de engenharia de
prompt do achado original**, só que sobrevivendo num subcaso que a correção não previu.

O docstring da própria correção (`core/locale.py::date_parse_hint`, linhas 90-93) afirma
que "quando concordam (datas ISO, ou todo dia > 12), as leituras são equivalentes" — essa
premissa é **empiricamente falsa** sempre que o dia é ≤ 12, ou seja, ~39% dos dias de
qualquer mês. Não é edge case, é o caso comum.

**Consequência direta em alucinação de narrativa** (o foco desta persona): o Science
Agent, alimentado com as datas já corrompidas (todas "viradas" para janeiro), produziu:

> "A análise diagnóstica da receita na região Sul mostra que, **em janeiro de 2026, a
> receita total foi de R$ 45.000**, com uma queda de R$ 3.000 em relação ao mês
> anterior. Isso indica uma **tendência de baixa** na receita..."

Nenhum desses números está correto: R$ 45.000 é a **soma das 3 linhas** (15.000+18.000+
12.000), apresentada como se fosse o total de um único mês; a queda real entre as linhas
2 e 3 é de R$ 6.000, não R$ 3.000. Ou seja, além do bug de datas, o LLM **alucinou** as
cifras específicas do "board-ready" pedido pelo prompt de teste — confiante e errado, o
padrão exato que esta auditoria foi desenhada para caçar.

**Gap adicional no guard-rail determinístico**: `_validate_narrative_consistency`
(`science.py`) valida a palavra de direção contra `predictions_df.select_dtypes(
include="number").columns[-1]` — a **última coluna numérica**, sem saber qual coluna a
narrativa realmente descreve. Para esta tabela diagnóstica (`revenue_brl,
previous_month_revenue, delta`), a última é `delta`, não `revenue_brl` — o check nunca
teve chance de pegar a alucinação de cifra porque ele não compara número citado × dado,
só direção × sinal de uma coluna escolhida por posição.

**ADR-037 (Reviewer) está habilitado em produção** (`AI_ETL_LLM_REVIEW_ENABLED=true`,
confirmado em `docs/CURRENT_STATE.md`), mas não consegui confirmar via UI se ele
sinalizou este caso — a aba Science/Advisor do runner de histórico não renderizou
conteúdo de forma confiável durante a sessão (provável instabilidade do
`claude-in-chrome`, não do produto); a evidência de narrativa/código veio da aba "Code",
que concatena tudo mas não exibe badges de `sanity_check`. **Não afirmo que o Reviewer
falhou aqui — não consegui verificar, e isso deveria ser conferido de novo com uma sessão
de browser estável.**

### 2. MÉDIO — recomendação de negócio de alta prioridade construída sobre nome de coluna não verificado, sem nenhuma ressalva de confiança

No run 2, o Advisor recomendou, com prioridade **"high"**:

> "Focar em campanhas de marketing direcionadas para o plano 'enterprise', destacando
> sua margem de lucro de R$ 2000.40." — segmento com **apenas 2 clientes**.

O prompt do Advisor nunca instrui a hedgear recomendações apoiadas em segmentos de N
muito pequeno, nem a questionar se uma coluna nomeada `profit_margin` de fato representa
uma margem (era, no dataset de teste, um valor monetário bruto por cliente — não uma
margem calculada). Isso é a mesma classe de achado "semantic slip" da auditoria de
2026-08-24 (rótulo de eixo "Categoria de Produto" inexistente) — reforça que o problema é
sistêmico (nenhum agente valida semântica de coluna contra o nome), não um caso isolado.

## Já era conhecido pelas auditorias anteriores? (sim/não + link)

- **Achado 1 (bug de datas) — PARCIALMENTE conhecido, mas a conclusão da auditoria
  anterior está incorreta.** `docs/CURRENT_STATE.md`, entrada "2026-09-04 (session 2)"
  descreve o bug original e o marca como corrigido ("Fixed by changing the prompt's
  canonical example..."). Este achado mostra que **a correção não fecha o caso comum**
  (dia ≤ 12 em formato ISO) — é uma continuação do mesmo bug-class, não um achado novo
  isolado, mas a alegação de "fixed" no doc está factualmente errada para este subcaso.
- **Achado 2 (hedging/direção em pergunta direcional)** — o mecanismo ADR-037 que existe
  hoje para isso (`reviewer.py`'s `llm_review_hedge`) já nasceu como resposta ao achado
  do audit de 2026-08-24 (`docs/work/2026-08-24-full-technical-product-audit.md`, linha
  173) — não é um achado novo; não consegui verificar ao vivo se ele dispara neste caso
  específico por instabilidade de browser.
- **Achado "semantic slip" de coluna (item 2 acima)** — mesma classe do achado de
  2026-08-24, linha 175 ("Analyst mislabeled a product-level chart axis..."); não
  corrigido desde então (nenhuma instrução de hedge por N pequeno ou verificação de
  nome-de-coluna foi adicionada aos prompts do Advisor/Analyst/Science).

## Recomendação

1. **Prioridade alta**: corrigir `date_parse_hint`/o padrão de código do Transformer
   para não usar `dayfirst=True` do pandas como "segunda leitura" quando a coluna já é
   detectavelmente ISO 8601 (`YYYY-MM-DD`) — nesse caso a leitura correta é sempre a
   `default_parsed`, e a comparação com `dayfirst_parsed` não deveria nem ocorrer (ela é
   estruturalmente enganosa para o parser do pandas, não só quando os dois "concordam").
   Adicionar um teste de unidade que rode literalmente o snippet gerado (não apenas
   verifique o texto do prompt) contra uma série com dia ≤ 12 em ISO, do jeito que
   `test_locale.py`/`test_transformer.py` fariam bem em cobrir.
2. Corrigir `_validate_narrative_consistency` para identificar a coluna que a narrativa
   realmente cita (ex.: procurar o nome da coluna mencionado no texto, ou usar a coluna
   de nível/valor primário do `task`, não `numeric_cols[-1]` por posição).
2. Verificar de novo, com uma sessão de browser estável, se o Reviewer (ADR-037) de fato
   sinaliza o caso de alucinação de cifra do Achado 1 — se não sinalizar, é um gap real
   do reviewer (ele testa consistência/hedging, não confere números citados contra a
   tabela).
3. Adicionar ao prompt do Advisor uma instrução de hedge por tamanho de segmento (N
   pequeno) e uma checagem leve de que nomes de coluna "carregados" (profit, margin,
   revenue) não são tratados como verdade absoluta sem contexto adicional.

Consumo de execução: 2 análises reais em `gpt-4o-mini` (~$0.0006/run cada, conforme
preço anunciado na própria UI) — dentro do orçamento de ~20 min solicitado.
