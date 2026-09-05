# Persona: Acessibilidade

## O que investiguei (páginas cobertas)

Sessão rodou sozinha no Chrome (sem disputa de aba com outras personas). Não notei nenhuma
instabilidade de ambiente (troca de página sozinha, conteúdo trocando) — a única coisa que
pareceu "estranha" no início (texto quase invisível no primeiro screenshot da landing page) foi
confirmada como um fade-in de animação em andamento, não um bug real: um segundo screenshot 2s
depois mostrou o texto com contraste normal. Registrado aqui só por transparência, não como achado.

Páginas cobertas (7, acima do mínimo de 5), com `read_page` (filter `interactive` e `all`),
inspeção de DOM via `javascript_tool` (label associations, computed contrast ratio via canvas,
`aria-live` regions), e navegação real por teclado (`Tab`) no formulário de `/app`:

- `/` (landing page)
- `/app` (formulário principal — Run an analysis)
- `/pipelines` (Scheduled pipelines)
- `/history`
- `/budget`
- `/secrets`
- `/data-export`

## Achados

### 1. `localeToggle.switchTo` — chave de i18n vazando como nome acessível (Média, já conhecido)

O botão de troca de idioma (EN/PT) tem `aria-label="localeToggle.switchTo"` **e** texto visível
literal `"localeToggle.switchTo"` em vez de uma tradução real, confirmado no DOM em `/`, `/app`,
`/pipelines`, `/history`, `/budget`, `/secrets`, `/data-export` — presente em todas as páginas
por ser parte do header global. Um leitor de tela lê essa string sem sentido em vez de algo como
"Switch to Portuguese".

Evidência (JS, `document.querySelector('button').outerHTML`):
```html
<button type="button" ... aria-label="localeToggle.switchTo" ...>
```

**Já era conhecido?** Sim — `docs/work/2026-08-24-full-technical-product-audit.md` linha 248 já
registrou exatamente esse achado ("fix the untranslated `localeToggle.switchTo` key", punch-list
Alta). Confirmado que **ainda não foi corrigido** 11 dias depois.

### 2. Upload de arquivo em `/app` é inacessível por teclado (Alta, novo)

O `<input type="file" id="file">` real tem `display:none` (confirmado via `getComputedStyle`),
o que o remove da ordem de tabulação mesmo com `tabIndex=0` no atributo. O elemento visível
("Click to select a file") é um `<label for="file">` — um padrão HTML válido para clique de
mouse, mas `<label>` não é um elemento nativamente focável/operável por teclado (Enter/Space
num `<label>` focado não aciona o `click()` do input associado, e o label nem entra na ordem de
tab). Testado ao vivo: percorrendo o formulário inteiro só com `Tab`, o foco pula do botão
"Open user menu" direto para o `textarea` "Or: manual spec" — o controle de upload nunca recebe
foco. Um usuário exclusivamente de teclado não consegue abrir o seletor de arquivo nesta tela.

Evidência:
```json
{"tabIndex":0,"display":"none", ...}  // input#file
```
```json
{"wrapperTabIndex": -1, "wrapperRole": null,
 "wrapperHTML": "<label for=\"file\" class=\"flex items-center gap-3 border border-dashed ...\">"}
```

**Já era conhecido?** Não encontrei menção a isso na auditoria de 2026-08-24 nem em nenhum outro
doc de `docs/work/`. É um achado novo desta rodada.

### 3. Tabela de `/history`: cada linha vira 5 links `<a>` idênticos, sem semântica de tabela (Média, novo)

A listagem de runs não usa `<table>`/`role="table"` — cada linha é um bloco de 5 `<a>` separados
(nome do arquivo, status, modelo, custo, data), todos apontando para o **mesmo** `href`
(`/history/<id>`). Navegando por teclado, cada linha gera 5 paradas de Tab consecutivas para o
mesmo destino, sem nenhum agrupamento (`role="row"`/`role="rowgroup"` ou ao menos um único link
que envolva a linha inteira com os demais campos como texto não-interativo). Um usuário de
leitor de tela ouve uma sequência repetitiva de "link, link, link, link, link" por linha, sem
contexto de coluna ("Status: Completed", "Model: gpt-4o-mini" etc.), e mais cliques de Tab do
que o necessário para chegar à linha seguinte.

Evidência (DOM de uma linha, 5 âncoras com o mesmo `href="/history/11d20b32-..."`):
```html
<a class="flex flex-col gap-0.5 px-2 py-2" href="/history/11d20b32-...">Read the file at ru...</a>
<a class="block" href="/history/11d20b32-...">completed</a>
<a class="block" href="/history/11d20b32-...">gpt-4o-mini</a>
<a class="block" href="/history/11d20b32-...">$0.003069</a>
<a class="block" href="/history/11d20b32-...">9/4/2026, 11:30:53 PM</a>
```

**Já era conhecido?** Não encontrei essa observação específica em auditorias anteriores.

### 4. Itens verificados e sem problema (para registro, não são achados)

- **Labels de formulário reais**: todos os campos de `/app`, `/pipelines`, `/secrets`,
  `/budget`, `/data-export` (file, textarea, input, select, checkbox) têm `<label for="id">`
  correto ou envolvem o input (`<label><input>texto</label>`) — confirmado via DOM, não só
  proximidade visual. O checkbox "Require approval before writing" em `/pipelines` aparece com
  nome "on" na leitura simplificada da árvore de acessibilidade da ferramenta, mas o DOM real
  mostra padrão de wrapping válido (`<label><input type=checkbox>Require approval before
  writing</label>`) — não é um bug real, é uma limitação da computação simplificada de nome da
  ferramenta de leitura de página.
- **Foco visível**: testado com `Tab` em `/app` — o anel de foco (box-shadow customizado) é
  visível em todos os controles alcançáveis (confirmado via zoom de screenshot no toggle
  EN/tema).
- **Contraste de cor**: medi 8 pares de texto/fundo reais (RGB via canvas, fórmula WCAG) em
  `/data-export` e no header global. Pior caso "Delete account" (vermelho sobre fundo quase
  preto): **6.2:1**, acima do mínimo AA (4.5:1). Textos secundários (`text-dim`): **8.13:1**.
  Nenhum par abaixo do limiar AA encontrado nas páginas verificadas — consistente com o que a
  auditoria de 2026-08-24 já havia medido (mínimo 4.74:1 em modo claro).
- **Toasts/notificações**: confirmado ao vivo — `/budget` tem uma `<section aria-live="polite">`
  real que recebe o texto do toast ("Budget cap updated.") no momento do disparo. Seria
  anunciado por um leitor de tela.

## Já era conhecido pelas auditorias anteriores? (resumo)

- Achado 1 (`localeToggle.switchTo`): **sim**, `docs/work/2026-08-24-full-technical-product-audit.md`
  (linha 248), ainda não corrigido.
- Achados 2 e 3 (upload por teclado; tabela de history sem semântica): **não encontrados** em
  nenhuma auditoria anterior verificada (`2026-08-24-full-technical-product-audit.md` e o
  Artifact "AI-ETL Platform Audit" listado — este último é um audit de capacidades/gaps de
  produto diferente, não cobre acessibilidade de forma equivalente).
- A auditoria de 2026-08-24 também registrou (não re-verificado nesta rodada por estar fora do
  escopo de tempo — exigiria disparar um run real): o stepper de progresso do agente
  (`agent-progress.tsx`) sem `aria-live`/`role="status"` durante execução ao vivo. Vale
  re-confirmar numa rodada futura que teste um run real ponta a ponta.

## Recomendação

Prioridade sugerida:
1. **Alta** — corrigir o upload de arquivo inacessível por teclado em `/app`: dar `tabindex="0"`
   + handler de `keydown` (Enter/Space aciona `input.click()`) ao `<label>` visível, ou remover
   o `display:none` do input real e estilizá-lo como visualmente oculto mas focável
   (`sr-only`/clip pattern) em vez de `display:none`.
2. **Alta** (reincidência) — traduzir de fato a chave `localeToggle.switchTo` — já está há 11
   dias na punch-list e continua no ar.
3. **Média** — dar semântica de tabela real a `/history` (`<table>`/`role="table"` com
   `role="row"`/`role="cell"`, ou pelo menos um único link por linha envolvendo os campos como
   texto estático) para eliminar os 5 tab-stops redundantes por run.
4. **Baixa** — re-verificar o `aria-live` do stepper de progresso do agente com um run real, já
   que a auditoria de 2026-08-24 apontou ausência total ali e isso não foi re-testado agora.
