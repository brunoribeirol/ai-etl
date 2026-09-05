# Persona: CTO/Founder

## O que investiguei

- `docs/CURRENT_STATE.md` completo (1870 linhas — todas as seções datadas de
  2026-08-13 até 2026-09-04, sessões 1-4).
- Artifact "AI-ETL Platform Audit" (`b859d4b5-89a4-4479-b372-af0b5c0ab62a`,
  auditado 2026-09-03, findings fechados 2026-09-04) — extraído o texto
  completo do HTML publicado, não apenas a preview.
- `docs/work/2026-08-24-full-technical-product-audit.md` (referenciado, não
  relido linha a linha — usado como baseline do que já era conhecido).
- Código real: `src/ai_etl/core/sandbox.py` (mecanismo de isolamento de
  execução), `src/ai_etl/api/routers/budget.py`, `infra/aws/terraform/*`,
  busca por qualquer integração de pagamento (`stripe`, `paddle`,
  `lemonsqueezy`) em todo o repo e no `frontend/package.json`.
- Rodei `uv run pytest tests/unit --collect-only -q` para checar a claim de
  contagem de testes contra o que está documentado.
- ADRs relevantes: ADR-003, ADR-007, ADR-032 (Decision 4), ADR-038, ADR-039,
  ADR-040, ADR-019 (referenciados via CURRENT_STATE, não relidos na íntegra
  por orçamento de tempo).

Não testei `ai-etl.vercel.app` ao vivo neste passe — o orçamento de tempo foi
direcionado para cruzar docs vs. código, que é o ângulo específico desta
persona (due diligence de arquitetura/negócio, não QA funcional).

## Achados

### 1. Não existe nenhuma camada de monetização real — "billing" é só *cost tracking* interno (severidade: alta para uma conversa de investidor, não é bug)

`grep -rli "stripe\|paddle\|lemonsqueezy"` no repo inteiro e no
`frontend/package.json` não retornou nenhum resultado. A seção "Cost, budget
& billing" do próprio audit artifact descreve exatamente isto:

> "Real cost tracking across OpenAI, Anthropic, Google — Per-run cost
> computed from the actual model used" / "Per-tenant monthly budget cap +
> pre-execution estimate — A run can be blocked before it starts if it would
> exceed the tenant's cap."

Lendo o código (`src/ai_etl/api/routers/budget.py`), `PATCH /budget` só seta
um teto (`monthly_budget_usd`) que bloqueia execuções — não existe cobrança,
checkout, plano pago, nem qualquer gateway de pagamento. É *cost governance*
(protege a própria conta de LLM de estourar), não *revenue infrastructure*.
Isso é honesto tecnicamente, mas o rótulo "billing" no audit e a seção
"Cost, budget & billing → Multi-provider, live" no dashboard de auditoria dão
a impressão, numa leitura rápida de due diligence, de que existe alguma
mecânica de cobrança de cliente. Não existe. Se este projeto for apresentado
como "SaaS" para um investidor, a primeira pergunta será "como vocês
cobram?" — e a resposta honesta hoje é "não cobramos, não há como".

### 2. "SaaS" com um único tenant real (o próprio dono) — já documentado, mas vale reforçar o enquadramento para due diligence

`docs/CURRENT_STATE.md` linha 1836 e linha 1095 já são explícitas: "Fine
while the owner is the only real tenant validating the mechanism". O
multi-tenancy (RLS, Clerk Organizations, secrets por tenant) é real e testado
em código, mas nunca foi exercitado com um segundo tenant de verdade — nem em
notificações (webhook global de deployment, não por tenant), nem em billing
(item 1 acima), nem em suporte/operações. Para uma banca de TCC isso é
perfeitamente adequado (é um protótipo funcional, não um produto em
produção com clientes). Para uma conversa de investidor, a arquitetura
multi-tenant é um ativo real, mas a frase correta é "arquitetura pronta para
multi-tenant, validada com um usuário" — não "SaaS multi-tenant em produção".

### 3. Isolamento de execução de código LLM-gerado tem bypass conhecido e o backend mais seguro está desligado por decisão de custo, não por engenharia (já conhecido, mas quero destacar a honestidade do comentário no código)

`src/ai_etl/core/sandbox.py` documenta no próprio docstring:

> "Security note: exec() with restricted globals can be bypassed via
> introspection (e.g., ().__class__.__mro__[1].__subclasses__())."

O backend padrão em produção é `"process"` (multiprocessing, sem
containerização real); `"docker"` existe mas não roda no Railway
(Docker-in-Docker não suportado); `"vercel"` (Firecracker microVM, a
mitigação real do bypass) está implementado mas **desabilitado** porque criar
um sandbox retorna 403 — o time Vercel está no plano Hobby e o dono já
recusou o upgrade para Pro. Isto é uma decisão de custo do dono, corretamente
registrada como "Owner decision... don't re-propose without asking" — mas do
ponto de vista de due diligence, o produto roda hoje em produção real
(`ai-etl.vercel.app`) executando código gerado por LLM num processo sem
isolamento de container, com um bypass documentado e não mitigado. Isso é
defensável numa banca de TCC (documentado, com plano de mitigação pronto e
código escrito) — seria um ponto duro de defender para um investidor técnico
sério perguntando "o que acontece se o LLM for manipulado via prompt
injection para gerar código malicioso hoje, em produção, contra dados reais
de um tenant pago". A resposta honesta é: "roda sem isolamento de container,
mitigação pronta mas não ligada por custo".

### 4. Contagem de testes no audit artifact não bate exatamente com a coleção real (severidade: baixa, provável imprecisão de doc, não achado de produto)

`docs/CURRENT_STATE.md` (seção "Owner's next steps", 2026-09-04 sessão 4)
afirma "1168 tests, 95.3% coverage". Rodei
`uv run pytest tests/unit --collect-only -q` e obtive **1144 tests
collected** apenas em `tests/unit/` (não inclui `tests/integration/` nem
`tests/e2e/`, que explicam a diferença de ~24 testes se forem contados à
parte, ou pode ser um drift entre o momento da doc e agora). Não é uma
discrepância grande o suficiente para indicar exagero deliberado, mas é o
tipo de número que uma due diligence técnica real checaria de imediato —
vale rodar `make test` completo (unit + integration) antes de citar o número
exato num pitch deck ou na banca.

### 5. Números do case study da TCC (OE4) estão desatualizados em relação à arquitetura atual — já conhecido e sinalizado como decisão pendente do dono

Confirmado em `docs/CURRENT_STATE.md`: "Those case-study numbers are from
2026-06-23 — before Clerk, RLS, approvals, multi-source, and everything
shipped since." Isto já está listado como "Needs your call" tanto no
CURRENT_STATE quanto no audit artifact ("TCC track"). Não é um achado novo,
mas reforço porque é exatamente o tipo de gap que uma banca notaria: os
resultados quantitativos citados no relatório técnico não correspondem ao
sistema que será demonstrado ao vivo.

### 6. RLS em Postgres/Supabase roda com `rolbypassrls=true` — decisão consciente, já documentada em ADR-032

`docs/CURRENT_STATE.md` linha 871 cita ADR-032 Decision 4: "keep
`rolbypassrls=true` (RLS stays a defense only against a leaked Supabase anon
key, not a second layer against an app-level `tenant_id` bug)". Ou seja, a
RLS não protege contra um bug de aplicação que esqueça o filtro
`tenant_id` — só protege contra vazamento da anon key. Isso é uma decisão
técnica válida e documentada, mas "Row-Level Security (Postgres) — a
restricted DB role backstops every tenant_id filter at the database level"
no audit artifact (linha "Auth, tenancy & RBAC") é uma frase que, lida
isoladamente por um investidor, sugere uma garantia mais forte do que a que
realmente existe. Vale ajustar o texto do audit/pitch para não prometer
"defense in depth completa contra bug de app" quando na prática é "defesa
contra uma classe específica de vazamento de credencial".

## Já era conhecido pelas auditorias anteriores?

- Achado 1 (ausência de monetização real): **não**, explicitamente — nenhuma
  das duas auditorias anteriores (2026-08-24 e 2026-09-03/04) nomeia a
  ausência de payment/billing como um gap. O audit artifact até rotula a
  seção como "Cost, budget & billing — Multi-provider, live", o que reforça
  a leitura equivocada em vez de sinalizar a lacuna.
- Achado 2 (single-tenant): **sim**, documentado extensivamente em
  `docs/CURRENT_STATE.md` (linhas 311, 416, 1095, 1836) e no audit artifact.
  Reforçado aqui só para o enquadramento de due diligence.
- Achado 3 (sandbox sem isolamento real em produção): **sim**, documentado no
  próprio código (`core/sandbox.py` docstring), em ADR-003/007/038/039, e no
  audit artifact ("Vercel Sandbox stays off... Not a bug — a standing
  decision"). Trago aqui só a moldura de "como isso soa numa due diligence
  técnica", que é diferente de "é um bug não documentado".
- Achado 4 (contagem de testes): **não** verificado explicitamente antes —
  é uma checagem nova, mas a discrepância é pequena e provavelmente
  metodológica (escopo unit vs. unit+integration), não um número inflado.
- Achado 5 (case study desatualizado): **sim**, é o item mais destacado como
  pendência do dono em ambas as fontes.
- Achado 6 (`rolbypassrls=true`): **sim**, documentado em ADR-032, mas a
  formulação do audit artifact (linha "Row-Level Security (Postgres)")
  overstate a garantia em relação ao que o ADR realmente decidiu.

## Recomendação

1. **Renomear a seção "billing" antes de qualquer conversa externa.** Trocar
   "Cost, budget & billing" por algo como "Cost governance" nos materiais
   voltados a investidor, e adicionar explicitamente uma linha "no payment
   processing exists yet" — não como bug, mas como roadmap item claro. Isso
   evita a pergunta pega ser feita ao vivo por quem já leu o audit.
2. **Não mudar a decisão do sandbox agora** (é uma decisão de custo válida),
   mas preparar uma resposta de 30 segundos para due diligence técnica: "o
   bypass é conhecido, a mitigação (Vercel Sandbox / Firecracker) está
   pronta em código, ligar custa X/mês, e hoje o único tenant é o próprio
   dono — o cálculo de risco muda no dia em que houver um segundo tenant
   pagante". Isso transforma um ponto fraco em prova de maturidade de
   engenharia (risco calculado, não ignorado).
3. **Ajustar a frase de RLS no audit/pitch** para refletir com precisão o
   que `rolbypassrls=true` realmente garante (defesa contra vazamento de
   anon key) vs. o que não garante (bug de app esquecendo `tenant_id`).
4. **Rodar `make test` completo (não só `tests/unit`) antes de citar
   "1168 tests" em qualquer documento externo** — a claim provavelmente está
   certa somando unit+integration, mas due diligence real checa números
   citados, então vale ter o comando rodado e o output salvo antes de
   apresentar.
5. Nenhuma ação de código é necessária agora — os achados 2, 3, 5 e 6 já são
   conhecidos e conscientemente aceitos pelo dono; o achado 1 é de
   enquadramento/comunicação, não de arquitetura; o achado 4 é uma checagem
   de precisão de doc, não um bug.
