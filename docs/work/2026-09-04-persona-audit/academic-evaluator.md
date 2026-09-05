# Persona: Avaliador Acadêmico

## O que investiguei (quais arquivos do vault e do repo)

**Repo:**
- `CLAUDE.md`, `docs/CURRENT_STATE.md` (linhas 1-150, seções 2026-09-04 sessões 1-4)
- `docs/work/2026-08-24-full-technical-product-audit.md` (seção "Academic evaluator" completa, para não repetir achados já registrados)
- `case_study/results/tabela-resultados.md`, `case_study/results/sprint8/` (README, `stability_summary.json`)
- `docs/adr/ADR-007-unified-sandbox-policy.md`
- `grep` em `src/ai_etl/audit/` por `stage_latencies`/`save_stage_latencies` (ADR-007)
- `grep` em `src/ai_etl/core/graph.py` por instrumentação de latência

**Vault (`~/Documents/Obsidian Vault/tcc/`):**
- `CONTEXT.md` (OE1-OE4 confirmados aqui, linhas 21-24)
- `artefact/decisions.md` (seção metodologia/DSR, linha ~31-35)
- `artefact/evaluation-metrics.md` (framework de 10 métricas, `status: proposed`, criado 14/08)
- `artefact/requirements.md` (RF/RNF, mapeamento RF×Agente)
- `writing/drafts/draft-product-vision.md` (§8, Agentic BI)
- `writing/drafts/draft-results.md` (§6, protocolo experimental e Tabela 1)
- `writing/drafts/draft-conclusion.md` (§7.2 Limitações, §7.3 Trabalhos Futuros)
- `mtime` de todos os arquivos acima via `stat`

Não usei a skill `search-vault` (fiz leitura direta via `Read`/`grep`, mais rápido para os arquivos específicos já apontados pelo prompt). Não houve problema de acesso ao vault.

## Achados (severidade + evidência concreta)

### 1. [JÁ CORRIGIDO, não é mais achado] Draft-product-vision §8 "Agentic BI como especulativo"
A auditoria de 2026-08-24 apontou isto como discrepância crítica-para-defesa. Hoje o arquivo já traz, no topo da própria seção:
> "**Nota de revisão (2026-08-26):** esta seção descrevia a camada Agentic BI como visão especulativa/trabalho futuro. Isso deixou de ser verdade — a camada está implementada e em produção desde o Sprint 21+..."
Corrigido 2 dias após o achado. Confirmo que o texto do corpo da seção (§8.1/8.2) já descreve a camada como "implementada e em produção", consistente com o código real (`src/ai_etl/agents/analysis/`). **Sem discrepância remanescente aqui.**

### 2. [MÉDIO, achado refinado] `artefact/evaluation-metrics.md` está desatualizado frente ao código — mais métricas instrumentadas do que o documento admite
O framework de 10 métricas (criado 2026-08-14, `status: proposed`, sem revisão desde então) lista a métrica 4 ("Latência total e por estágio") como **"Falta instrumentar (Sprint 2/3)"**. No entanto, `docs/adr/ADR-007-unified-sandbox-policy.md` (também datado 2026-08-14, "Sprint 2") documenta a decisão de implementar exatamente essa instrumentação, e o código confirma que foi feita de verdade:
- `src/ai_etl/audit/models.py:97` — tabela `stage_latencies` real, com índice `ix_stage_latencies_tenant_stage`.
- `src/ai_etl/audit/db/runs.py:422` — `save_stage_latencies()`, docstring "Persist per-stage wall-clock durations... (ADR-007)".
- `src/ai_etl/audit/db/health.py:64,103-107` — consumida de fato (soma de `duration_seconds` por run).

A auditoria de 2026-08-24 já reportou "apenas 3 de 10 métricas instrumentadas hoje" — meu achado é que esse número provavelmente já estava impreciso naquela data (métrica 4 tinha acabado de ser implementada, mesmo Sprint), e continua impreciso agora: o documento-fonte da seção de Avaliação do TCC não reflete o estado real do código. Se a seção de Metodologia/Avaliação do TCC for escrita citando esse arquivo do vault como estava, vai subestimar o que já foi instrumentado. Não verifiquei as métricas 5-10 uma a uma (fora do orçamento de tempo), então não posso afirmar quantas das 10 estão prontas hoje — só que o documento não foi atualizado desde a criação e pelo menos 1 item nele está desatualizado.

### 3. [JÁ CONHECIDO, confirmado que persiste] Case study com dados de 2026-06-23, anterior a boa parte do que existe hoje
`case_study/results/tabela-resultados.md` (linha 4: "**Data:** 2026-06-23") e `writing/drafts/draft-results.md` (§6.1, "**Datas:** 2026-06-23") — ambos com o mesmo `mtime` (23/06 no draft) — descrevem os 15 runs oficiais do estudo de caso. `docs/CURRENT_STATE.md` (linha 27-31, sessão 4 de hoje) já documenta isto explicitamente como item aberto de decisão do owner: *"aqueles números são de 2026-06-23, antes de Clerk/RLS/approvals/multi-source e tudo que foi lançado desde então"*. Confirmo que o gap **ainda existe sem alteração** — `draft-results.md` não foi tocado desde 23/06, e o `CURRENT_STATE.md` de hoje trata isso como decisão pendente do owner (rodar de novo o case study ou não), não como algo resolvido. Isto já era conhecido tanto pela auditoria anterior (implicitamente, via o registro em `CURRENT_STATE.md`) quanto pelo próprio owner — não é um achado novo, mas confirmo que segue real e não mitigado.

### 4. [SEM DISCREPÂNCIA] Metodologia DSR e Limitações/Trabalhos Futuros seguem honestos
- `artefact/decisions.md` (linha ~31-35): decisão explícita de usar DSR apenas como *referencial* (Hevner et al.), não como classificação metodológica principal, "porque o professor não inclui DSR na lista de procedimentos técnicos aceitos" — mesma distinção que a auditoria anterior recomendou tornar explícita na seção de Metodologia do TCC. Não verifiquei o texto final da seção de Metodologia (não está em `writing/drafts/` sob esse nome) para confirmar se a redação já reflete essa distinção com a precisão que `decisions.md` pede — permanece como pendência de redação, não uma nova inconsistência de fato.
- `writing/drafts/draft-conclusion.md` §7.2/§7.3: a limitação do sandbox `exec()` não isolado (contornável via introspecção Python) é descrita com precisão e bate com o estado real do código e com ADR-032 (aceito, não corrigido) e com a decisão do owner de não pagar o Vercel Sandbox Pro. Nenhuma alegação otimista demais encontrada aqui.

### 5. OE1-OE4 — evidência real por objetivo (checagem rápida)
Confirmados nomeados formalmente em `CONTEXT.md:21-24`:
- OE1 (mapear estado da arte) e OE2 (especificar arquitetura) — vivem em `research/`/`artefact/architecture.md`, fora do escopo desta checagem de 20 min focada em código vs. narrativa.
- OE3 (implementar versão funcional com código auditável) — fartamente evidenciado pelo próprio repo (`log_action()`, `audit/`, `make check` limpo conforme sessões recentes).
- OE4 (avaliar em estudo de caso com fontes heterogêneas) — evidência real existe (`case_study/results/`, `sprint8/stability_summary.json`, `model_comparison_2026-08-23_sonnet_only/`), mas a *tabela oficial* dos 15 runs (a que o draft de resultados cita) é a de 23/06 — ver achado #3. `sprint8`/`model_comparison_2026-08-23` são dados adicionais mais recentes de sprints técnicos, não substituem formalmente a tabela citada no draft de resultados.

## Já era conhecido pelas auditorias anteriores? (sim/não + link se sim)

- Achado #1 (draft-product-vision §8): **sim, já conhecido** — era o achado central da persona Academic Evaluator em `docs/work/2026-08-24-full-technical-product-audit.md` (linha 129) — e **já foi corrigido** em 2026-08-26, confirmado por mim hoje.
- Achado #2 (evaluation-metrics.md desatualizado): **parcialmente conhecido** — a auditoria de 08-24 já dizia "apenas 3/10 instrumentadas" (mesma seção, linha 132), mas não notou que a métrica de latência específica já tinha sido implementada via ADR-007 no mesmo Sprint em que o documento foi escrito. Refinamento do achado anterior, não um achado inteiramente novo.
- Achado #3 (case study desatualizado): **sim, já conhecido** — registrado como item aberto em `docs/CURRENT_STATE.md` (sessão 4, hoje), tratado como decisão pendente do owner. Não estava explícito na seção Academic Evaluator da auditoria de 08-24, mas está documentado no repo.
- Achado #4/#5: nada de novo — narrativa honesta confirmada, sem discrepância.

## Recomendação

1. **Baixo esforço, alto valor para a defesa:** revisar `artefact/evaluation-metrics.md` antes de escrever a seção de Avaliação — marcar a métrica 4 (latência) como instrumentada de verdade (ADR-007 + `stage_latencies`), e fazer uma checagem rápida (fora do escopo desta auditoria) de quantas das 10 métricas realmente têm dado hoje, para não subestimar o que já existe na redação final.
2. **Decisão do owner, não uma correção de código:** decidir explicitamente se o capítulo de resultados vai usar a tabela de 23/06 como está (com uma nota clara de que antecede boa parte do sistema atual) ou se vale re-rodar o case study antes de escrever o capítulo — `CURRENT_STATE.md` já trata isso como pendência conhecida, só reforço que segue sem solução até hoje.
3. Nenhuma ação necessária sobre draft-product-vision (já corrigido) nem sobre a seção de limitações/trabalhos futuros (já honesta e alinhada ao código).
