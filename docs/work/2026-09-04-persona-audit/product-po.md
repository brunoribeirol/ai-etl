# Persona: Product/PO

## O que investiguei

1. Lista completa de rotas reais do backend (`grep "@router\." src/ai_etl/api/routers/*.py` +
   `@app.get("/config")` em `main.py`) cruzada rota a rota com toda chamada real do frontend
   (`apiFetch`/`authedFetch`/`fetch` em `frontend/src`, cobrindo `lib/api.ts` — server-side — e
   `lib/authed-fetch.ts` — client-side — além dos `fetch` diretos ainda não migrados em
   `onboarding-checklist.tsx`, `locale-toggle.tsx`, `run-form.tsx`).
2. Para cada rota "aparentemente sem consumidor", inspecionei o componente que deveria usá-la
   para distinguir 3 casos: (a) rota morta de verdade, (b) rota redundante porque o dado já vem
   embutido em outra resposta (ex.: `GET /pipelines` já traz `llm_provider`/`notification_channel`
   no payload da lista), (c) decisão de produto documentada no próprio código para não consumir.
3. Naveguei ao vivo em `ai-etl.vercel.app` (sessão Clerk já ativa) — `/summary`, `/history`,
   `/pipelines` — para confirmar com a UI real (não só grep) se "quality rules customizadas" e
   "drift threshold por pipeline" (citados em `docs/CURRENT_STATE.md` Sprint 16/14) têm campo na
   tela de criação/edição de pipeline.
4. Cruzei cada achado com `docs/work/2026-08-24-full-technical-product-audit.md` (linha 117) para
   não reportar como novo algo já catalogado — e para checar se algo catalogado como gap lá já foi
   corrigido desde então (a instrução do prompt pedia explicitamente não confiar cegamente nela).

## Achados (severidade + evidência)

### 1. `drift_threshold_pct` por pipeline — sem nenhum campo na UI (Média, achado NOVO)
`CreatePipelineRequest`/`UpdatePipelineRequest` (`src/ai_etl/api/routers/pipelines.py`) aceitam
`drift_threshold_pct` (default 20.0) desde a Sprint 14 (ADR-018). `grep -rn "drift"
frontend/src` não retorna **nenhuma** ocorrência funcional em `pipelines-manager.tsx` — nem no
formulário de criação nem no de edição. Confirmado ao vivo: a tela `/pipelines` (formulário "New
scheduled pipeline") mostra Name, Source type, Spec, Business question, Schedule, Quality rules
(JSON) e Require approval — **sem** campo de drift threshold. Todo pipeline criado pela UI fica
travado no default de 20%; a única forma de mudar é chamar `PATCH /pipelines/{id}` direto na API.
A auditoria de 2026-08-24 não capturou esse gap especificamente (ela olhou paridade rota↔chamada,
não campo↔schema dentro de uma rota já consumida — `PATCH /pipelines` é chamada, só que sem esse
campo no corpo).

### 2. `GET /pipelines/{id}/llm-config` e `GET /pipelines/{id}/notification-config` — rotas sem consumidor, mas não é gap de produto (Baixa, achado refinado)
Nenhum componente chama esses dois `GET`s. Não é um buraco de UX: `startEdit()` em
`pipelines-manager.tsx:149-165` já preenche `llmProvider`/`llmModel`/`notificationChannel`/
`notificationConfigured` a partir do próprio objeto retornado por `GET /pipelines` (lista) — os
`PUT`s correspondentes são de fato usados (`ModelPicker`, `NotificationConfig`). É superfície de
API redundante/morta, não funcionalidade inacessível pela interface.

### 3. `POST /llm/test-connectivity`, `POST /runs/estimate`, `GET /pipelines/llm/allowed-models`, `GET /tenant/locale` — já conhecidos, ainda sem consumidor (Baixa/Média, JÁ CONHECIDO)
Confirmados ainda sem chamada no frontend hoje. Já catalogados na auditoria de 2026-08-24 (linha
117: `llm/test-connectivity`+`allowed-models`, `runs/estimate` "zero frontend consumer"). Dois
deles são decisão de produto documentada no próprio código-fonte, não omissão:
`cost_estimation.py` diz explicitamente "frontend work is out of scope for this sprint"; o picker
de modelo usa `lib/model-reference-data.ts` (estático) em vez de `GET /llm/allowed-models` por
decisão de 2026-08-23 registrada no docstring do arquivo. `GET /tenant/locale` (only-read do
locale atual) não tinha sido citado nominalmente na auditoria anterior, mas segue o mesmo padrão
de "endpoint de leitura que a UI não precisa" — o cookie local já é a fonte de verdade da UI, e
`PATCH /tenant/locale` é chamado.

### 4. `admin/*` e `tenant/export` — a auditoria de 2026-08-24 estava ERRADA/DESATUALIZADA nesse ponto (achado da checagem cruzada, não um problema do produto)
A auditoria de 2026-08-24 (linha 117) listava "a entire `admin/*` surface" e `tenant/export` como
sem consumidor no frontend. Isso **não é mais verdade** (e pode já não ter sido verdade em
2026-08-24 — não investiguei quando mudou): `admin-manager.tsx` chama `GET /admin/audit-log`,
`GET /admin/tenants`, `GET /admin/tenants/{id}/runs`, `GET /admin/tenants/{id}/budget`; e
`data-export-manager.tsx` chama `GET /tenant/export`. Reporto isso não como achado de produto, mas
como confirmação de que a auditoria anterior não deve ser citada sem re-verificar — exatamente a
regra que este prompt pediu para aplicar.

### 5. Quality rules e require-approval — configuráveis pela UI, mas via textarea JSON crua (Baixa, JÁ CONHECIDO em espírito)
`quality_rules` é editável na UI, só que como `<Textarea>` de JSON livre
(`pipelines-manager.tsx:436-438`, `qualityRulesText`) — sem construtor de regra por campo/dropdown.
Funcional, mas exige o usuário saber a forma exata do schema (`column`/`operator`/`value`/
`severity`). Não é um "recurso sem UI", é um recurso com UX de baixo nível — não achei essa
distinção registrada explicitamente na auditoria anterior, mas é consistente com o padrão de
"backend-first, frontend depois" que ela já descreve em vários pontos.

## Já era conhecido pelas auditorias anteriores?

- Achado 1 (drift threshold sem UI): **Não** — novo.
- Achado 2 (GET llm-config/notification-config mortos): **Parcial** — a auditoria de 2026-08-24
  citava "notification config" sem UI de forma genérica; a investigação de hoje mostra que isso já
  está errado pela metade (o `PUT` é usado, só os `GET`s é que sobram).
- Achado 3 (test-connectivity/estimate/allowed-models): **Sim** —
  `docs/work/2026-08-24-full-technical-product-audit.md:117`.
- Achado 4 (admin/tenant-export não são mais gaps): não é um achado de produto, é uma correção do
  registro — a auditoria de 2026-08-24 está desatualizada nesse ponto específico.
- Achado 5 (quality rules via JSON cru): **Não** citado explicitamente antes.

## Recomendação

1. Adicionar campo de "Drift alert threshold (%)" ao formulário de criação/edição de pipeline —
   é o único caso desta rodada de um recurso genuinamente "backend-complete, zero UI" (usuário não
   consegue configurar isso hoje sem chamar a API direto). Esforço pequeno: um `<Input type="number">`
   + `drift_threshold_pct` no payload de `POST`/`PATCH /pipelines`.
2. Não investir em `GET /pipelines/{id}/llm-config`/`notification-config` — considerar removê-los
   do backend (dead code) na próxima limpeza técnica, já que o dado vem pela listagem.
3. `POST /runs/estimate` e `POST /llm/test-connectivity` seguem sendo trabalho de frontend
   pendente, mas ambos já são decisão consciente registrada em código — não é uma surpresa, é
   backlog conhecido. Vale reavaliar prioridade agora que o produto está "functionally done"
   segundo `CURRENT_STATE.md` — um botão "Test connection" no `ModelPicker` teria valor de produto
   real (hoje o usuário só descobre que a chave da API está errada quando o pipeline falha).
4. Corrigir o registro: a auditoria de 2026-08-24 está desatualizada quanto a `admin/*` e
   `tenant/export` — não citar mais esses dois como gap em relatórios futuros sem re-checar.
