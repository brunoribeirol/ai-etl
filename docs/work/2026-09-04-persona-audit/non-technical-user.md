# Persona: Usuário não-técnico

Auditoria realizada em 2026-09-04, navegando https://ai-etl.vercel.app do zero, sem ler
código-fonte ou documentação do projeto — só o que a própria interface mostra.

## Jornada que tentei fazer

1. Abrir a landing page (`/`) e entender o que o produto faz.
2. Clicar em "Sign in and run a pipeline".
3. Subir um CSV fictício de vendas (5 linhas: data, produto, quantidade, preço, cidade) e
   fazer uma pergunta de negócio ("Quais produtos venderam mais e em qual cidade?").
4. Escolher o modelo de IA mais barato disponível.
5. Rodar a análise e tentar ver o resultado.
6. Navegar por History, Pipelines, Summary, Budget e Secrets do mesmo jeito ingênuo.

O login foi automático — uma sessão Clerk já estava ativa no Chrome compartilhado desta
máquina, então nunca vi a tela de cadastro/senha. Isso é bom para quem já tem conta, mas
significa que **não testei** a primeira experiência real de um usuário novo (criar conta,
confirmar e-mail, etc.) — vale testar isso separadamente com uma sessão limpa.

## Pontos de confusão/atrito

1. **Landing page fala a língua de quem já entende ETL, não a minha.**
   Onde: `/`.
   Esperado: uma frase simples tipo "suba uma planilha e receba um relatório".
   O que vi: "Describe the pipeline. The agents build it — you audit it.", e mais abaixo um
   parágrafo comparando o produto com "Code Interpreter", citando "Transformer, Analyst,
   Science" e uma pirâmide "Bronze → Silver → Gold → Science → Advisor" sem nenhuma explicação
   do que essas palavras significam para mim. Se eu não soubesse o que é um "pipeline" eu já
   teria fechado a aba aqui. A própria página admite que é "Built for two people": um
   "technical operator" e um "executive consumer" — e eu, usuário curioso sem contexto, não
   sou nenhum dos dois.

2. **Escolha de modelo de IA exige entender jargão de engenharia interna.**
   Onde: tela "Run an analysis", seção "AI model (optional)".
   Esperado: algo como "rápido e barato" vs. "mais caro e preciso".
   O que vi: cartões com preço por milhão de tokens de input/output, "quality score 88.0",
   e frases de changelog interno tipo *"3/3 real runs completed after the markdown-fence fix
   (PR #109)"* e *"after a temperature-parameter fix"*. Isso é claramente texto de debug/nota
   de engenharia vazando para a interface de produção — não decidi qual modelo escolher por
   entender a explicação, decidi só porque o número do preço era o menor.

3. **O campo "Business question" que preenchi não foi salvo.**
   Onde: formulário "Run an analysis".
   Esperado: minha pergunta ("Quais produtos venderam mais e em qual cidade?") aparecer
   preenchida quando cliquei para rodar.
   O que vi: ao rolar a tela de novo (depois de subir o arquivo, o layout mudou de posição),
   o campo voltou a mostrar só o texto de exemplo cinza ("Which products sell the most?"),
   ou seja, vazio. A análise rodou mesmo assim, mas não tenho certeza se minha pergunta foi
   realmente considerada — a tela não confirma em nenhum momento "sua pergunta foi: ...".

4. **Nenhuma confirmação de que o arquivo foi lido corretamente.**
   Onde: campo de upload.
   Esperado: algo como "5 linhas, 5 colunas detectadas" ou uma prévia da tabela.
   O que vi: só o nome do arquivo aparece ("vendas_ficticias.csv"). Não sei se o sistema
   entendeu as colunas certas antes de eu clicar em "Analyze data" — só descubro depois,
   quando (e se) o resultado aparecer.

5. **Nomes das etapas do processamento não são explicados durante a execução.**
   Onde: barra de progresso da tela de execução.
   Esperado: "Lendo arquivo... Limpando dados... Gerando resultado..."
   O que vi: uma barra com os rótulos "Silver", "Planner", "Gold / Science", "Advisor" — os
   mesmos termos técnicos da landing page, sem tooltip ou legenda. Também vi uma linha de log
   em português no meio de uma interface em inglês: *"Extractor — Extraindo e inspecionando
   os dados... (3.0s)"* — mistura de idioma que também é confusa.

6. **Clicar num resultado no histórico não leva de forma confiável para o resultado.**
   Onde: página "History".
   Esperado: clicar na linha da minha análise mostra o resultado.
   O que vi: comportamento inconsistente — às vezes o clique não fazia nada visível, às vezes
   abria uma aba nova mostrando de novo o formulário "Run an analysis" (não o resultado), e
   quando finalmente abri a página de detalhe de uma execução antiga, as abas "Silver /
   Pipeline / Gold / Science / Advisor / Code" apareciam, mas o conteúdo da aba "Silver" veio
   completamente em branco, sem nenhuma mensagem.

7. **Atualizar a página ou entrar direto num link te joga de volta para o início.**
   Onde: qualquer página que não seja a inicial (`/summary`, `/history/<id>`).
   Esperado: se eu atualizar a página em "Summary" ou "History", continuo lá.
   O que vi: digitar a URL direto no navegador (ou recarregar) sempre me devolvia para a tela
   "Run an analysis". Isso significa que eu não posso favoritar uma página específica nem
   compartilhar um link de um resultado com um colega — ele vai cair na tela de upload.

8. **O menu superior às vezes destaca a aba errada.**
   Onde: barra de navegação (Summary, Budget, Secrets, etc.).
   Esperado: clicar em "Summary" mostra a página Summary com "Summary" destacado.
   O que vi: por três vezes, cliquei num item do menu e o nome ficou destacado como "ativo",
   mas o conteúdo da tela continuou sendo o da página anterior (cliquei em "Summary" e vi o
   formulário de "Run an analysis"; cliquei em "Budget" e vi a tela de "Secrets"; cliquei de
   novo em "Summary" e continuei vendo "Budget"). Tive que clicar de novo ou recarregar para
   a tela realmente mudar. Para mim, sem saber o que é uma "rota" ou "SPA", isso simplesmente
   parece que o site "trava" ou "não responde ao clique".

9. **Uma execução antiga apontada pelo sistema deu erro sem explicação de próximo passo.**
   Onde: tentativa de reabrir uma execução em andamento.
   Esperado: ou o resultado, ou uma mensagem clara tipo "essa análise ainda não terminou,
   volte em alguns segundos".
   O que vi: `Error: Run not found.` — texto vermelho seco, sem botão de voltar, sem sugestão
   do que fazer a seguir.

## O que funcionou bem, sem eu precisar pensar

- **Login automático** foi transparente (sessão já ativa) — não posso avaliar a tela de
  cadastro, mas o fato de eu cair direto na ferramenta depois de clicar em "Sign in" foi bom.
- **Upload de arquivo** foi simples: um clique, escolher o CSV, pronto — nenhuma fricção
  técnica aí (arrastar/soltar ou seletor de arquivo, comportamento padrão esperado).
- **Página de Budget** é clara e direta: mostra limite mensal, quanto já gastei e um status
  ("Within cap") em linguagem simples, com um campo óbvio para definir um teto de gasto.
- **Página de Secrets** também é bem explicada para o que se propõe: "the value is never
  shown again once saved" deixa claro o comportamento de segurança sem jargão excessivo.
- A análise que rodei de fato **completou com sucesso** (status "Completed" no histórico, com
  custo real de ~$0,003 registrado) — ou seja, o motor por trás funciona; o problema não foi
  "não funcionou", foi "não entendi o que estava acontecendo nem consegui ver o resultado com
  confiança".

## Recomendação

Prioridade alta, pela ordem do que mais quebraria a confiança de um usuário não-técnico:
1. Corrigir a navegação (item 8) — clicar num menu tem que mudar a tela, sempre. Esse é o
   tipo de bug que faz alguém achar que o site "quebrou" e desistir.
2. Fazer deep-link/refresh funcionar (item 7) — hoje é impossível compartilhar ou favoritar
   uma página específica.
3. Mostrar o resultado da análise de forma confiável e legível a partir do histórico (itens 6
   e 9) — sem isso, o produto entrega valor mas o usuário nunca consegue "ver a resposta".
4. Traduzir a landing page e os rótulos de progresso para uma linguagem sem jargão técnico, ou
   pelo menos adicionar tooltips explicando "Silver/Gold/Science/Advisor" e o que significa
   "spec" — hoje a página já admite que existe um público não-técnico ("executive consumer")
   mas não escreve para ele.
5. Remover do texto voltado ao usuário qualquer referência interna de engenharia (PR #109,
   "temperature-parameter fix") — isso deveria estar num changelog interno, não na tela de
   escolha de modelo.
6. Confirmar visualmente o que foi entendido do upload (linhas/colunas) e se a "business
   question" foi realmente registrada antes de rodar, para o usuário rodar com confiança.
