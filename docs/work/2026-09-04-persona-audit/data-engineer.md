# Persona: Data Engineer

**Nota sobre a tentativa anterior:** a rodada anterior desta persona falhou por disputa de aba
compartilhada com 11 outras personas em paralelo e depois por rate-limit de sessão — relatada
com honestidade, sem achados live. Esta rodada rodou sozinha, em aba dedicada, sem nenhuma
disputa. Todos os 4 arquivos de teste foram de fato subidos pelo formulário Run real em
`https://ai-etl.vercel.app/app`, com `gpt-4o-mini`, e conferidos linha a linha na aba de detalhe
do History (abas Pipeline/Code/Gold da própria UI). Nenhum achado abaixo é hipótese de leitura
de código — todos vêm de runs reais, com link.

## O que investiguei

**1. `vendas_multisheet.xlsx`** — 3 abas Excel (`Vendas2025`: produto/preco/qtd, 3 linhas;
`Estoque`; `Metas`), sem campo para escolher a aba no formulário.
Run: https://ai-etl.vercel.app/history/a849c5bb-322b-4d9e-9ca6-61123a9b58d3

**2. `vendas_br_edge.csv`** — 6 linhas, coluna `preco` em moeda BR com símbolo e separador de
milhar (`"R$ 1.234,56"`), datas em 3 formatos misturados na mesma coluna
(`02/03/2026`, `2026-03-15`, `21-03-2026`, ...), `"N/A"` em `quantidade`, coluna `observacao`
100% vazia.
Run: https://ai-etl.vercel.app/history/6ac431ba-6f41-4a41-b1c8-a90c6b12f36e

**3. `pedidos_aninhado.json`** — array de 3 objetos no nível superior, cada um com `cliente`
(objeto aninhado com `endereco` aninhado dentro) e `itens` (array de objetos) — JSON
genuinamente não-flat.
Run: https://ai-etl.vercel.app/history/f5e03cf9-a28c-4459-ab8f-6bae01cd5798

**4. `funcionarios.docx`** — heading + parágrafo de texto corrido + uma tabela real do Word
(4 colunas: `Nome`, `Cargo`, `Salario` em moeda BR, `Data Admissao` em 2 formatos diferentes
incluindo um ISO já limpo `2019-06-15`).
Run: https://ai-etl.vercel.app/history/1191bb5a-7176-4fd1-b51c-b16b97725b3b

## Achados (severidade + evidência concreta)

### 1. [Confirmado ao vivo — Alto] Excel multi-aba falha sempre, sem opção de escolher a aba

Run `a849c5bb...`: Orchestrator rodou (2.52s), Extractor falhou em 0.30s, run marcado `Failed`,
sem custo de LLM registrado (a falha é determinística, antes de qualquer chamada real relevante).
O plano gerado pelo Orchestrator nem tem como especificar qual aba usar — o schema do plano não
tem esse campo. Único caminho: usuário reformatar o arquivo antes de subir (mandar apenas 1
aba), o que não é comunicado em lugar nenhum do formulário (que anuncia "Excel" como suportado
sem ressalva). Confirma a hipótese 1 da rodada anterior.

### 2. [Confirmado ao vivo — Crítico] Moeda BR com `R$` quebra o parsing por um bug de escape de string, e o bug se repete em 2 lugares diferentes do código gerado

Run `6ac431ba...` (CSV): coluna `preco` (formato `"R$ 1.234,56"`) virou **100% null** depois do
Transformer — não é corrupção parcial, é a coluna inteira perdida, incluindo valores sem `N/A`.
O código Python realmente gerado pelo Transformer (visível na aba Code do run):

```python
df['preco'] = df['preco'].str.replace('R\$ ', '').str.replace('.', '').str.replace(',', '.')
df['preco'] = pd.to_numeric(df['preco'], errors='coerce')
```

O bug: `'R\$ '` é uma string Python comum (não raw-string), e `\$` não é um escape válido — o
Python mantém a barra invertida literal. Ou seja, o código procura por `R\$ ` (com barra
invertida) numa string que na verdade é `R$ ` (sem barra). O replace nunca casa, o prefixo
`R$ ` nunca é removido, e depois de remover o ponto de milhar e trocar vírgula por ponto o valor
final ainda é algo como `"R$ 1234.56"` — que `pd.to_numeric(errors="coerce")` não consegue
converter, virando `NaN` para **toda** a coluna, run a run, de forma determinística (não é
"imprevisível" como a hipótese original supunha — é sempre null).

O mesmo bug de escape se repetiu, **de forma independente**, no run `1191bb5a...` (DOCX), desta
vez no código do Analyst (camada Gold), e desta vez travando a análise inteira em vez de só gerar
null:

```python
gold_df['salario'] = gold_df['salario'].str.replace('R\$ ', '').str.replace('.', '').str.replace(',', '.').astype(float)
```

Erro real capturado na UI (aba Gold desse run):
`could not convert string to float: 'R$ 5500.00'` — o `.astype(float)` sem `errors="coerce"`
faz a análise "Qual é a média salarial dos funcionários?" falhar por completo, com a UI mostrando
"Não foi possível gerar a análise automaticamente."

Duas ocorrências independentes do mesmo padrão de bug, em dois arquivos diferentes, na mesma
sessão, pelo mesmo modelo (`gpt-4o-mini`) — não é acaso isolado, é um padrão que o LLM reproduz
de forma consistente para moeda BR com o prefixo `R$`.

### 3. [Confirmado ao vivo — Alto] JSON aninhado não falha "silenciosamente" — derruba o Transformer inteiro

Run `f5e03cf9...`: diferente da hipótese original ("vira string tipo dict"), o que de fato
aconteceu foi: Extractor rodou rápido e sem erro (0.01s, aparentemente leu o JSON sem problema),
mas o Transformer rodou por 12.53s (indicando que chamou o LLM e tentou executar código gerado)
e falhou por completo — sem quality report, sem Gold, sem Silver, e a aba Code mostra
"No code available for this run.", ou seja nem o código gerado foi persistido. Resultado: run
`Failed`, custo "—" (não cobrado, ou não registrado). Não há visibilidade na UI de qual exceção
especificamente ocorreu — seria necessário acesso a logs de servidor para o traceback exato, o
que está fora do escopo desta rodada (browser-only). Ainda assim, o achado é claro: hoje, JSON
genuinamente aninhado (objeto dentro de objeto, array de objetos dentro de cada registro) **não
tem nenhum caminho de sucesso** no pipeline real — nem sucesso com dado ruim, nem erro
comunicado ao usuário além de "Failed".

### 4. [Confirmado ao vivo — Baixo, mas achou 1 bug de data real] DOCX com tabela: extração da tabela funciona bem; datas mistas (incluindo ISO) quebram de novo

A extração da tabela do Word funcionou corretamente — Extractor rodou em 1.69s e produziu as 4
colunas e 3 linhas exatamente como estavam na tabela original (`nome`, `cargo`, `salario`,
`data_admissao`), sem perder a tabela. Isso confirma o achado 4 da rodada anterior (não é bug).

Porém: a coluna `data_admissao` tinha um formato ISO já limpo (`2019-06-15`, linha "Maria
Souza") misturado com dois formatos ambíguos dd/mm/yyyy. Resultado real na aba Silver do run
`1191bb5a...`: a data de Maria Souza **virou vazia (null)**, enquanto as duas datas ambíguas
foram parseadas corretamente. Quality report confirma: `null on data_admissao — null ratio:
33.3%`. Pelo código-fonte do Transformer visível no run (aba Code), a causa é: o código tenta
primeiro `pd.to_datetime(..., format="%Y-%m-%d")` estrito; como nem todas as datas batem nesse
formato, cai para tentar `dayfirst=True` vs `dayfirst=False` inteiros na coluna e escolhe o que
tiver menos NaN — mas o pandas, ao não receber um `format` explícito numa coluna com formatos
misturados, infere o formato a partir do primeiro valor não-nulo e aplica esse mesmo formato à
coluna inteira, descartando (`NaT`) qualquer valor que não bata nesse formato específico — mesmo
que aquele valor individual fosse perfeitamente parseável sozinho. Ou seja: **o padrão de código
"tenta os dois `dayfirst`, escolhe o que tiver menos NaN" não resolve o caso de formatos
misturados dentro da mesma coluna** — só resolve quando a coluna inteira tem um único formato
consistente. Isso é uma variante nova do bug de datas pt-BR (a mesma família documentada e
corrigida em `docs/CURRENT_STATE.md`, sessão 2 de 2026-09-04, e possivelmente tocada de novo pela
PR #191 mencionada na tarefa) — mas especificamente para colunas com formatos **misturados**, que
é exatamente o tipo de dado sujo real que aparece em planilhas/tabelas Word preenchidas
manualmente por pessoas diferentes ao longo do tempo. Vale a pena checar se PR #191 cobre este
caso específico (formato misto, não só ISO vs BR).

## Já era conhecido pelas auditorias anteriores? (sim/não + link)

- **Achado 1 (Excel multi-aba sempre falha):** não. Confirma, agora ao vivo, a hipótese
  levantada por leitura de código na tentativa anterior desta mesma persona
  (`docs/work/2026-09-04-persona-audit/data-engineer.md`, versão anterior). Não consta em
  `docs/CURRENT_STATE.md`.
- **Achado 2 (bug de escape `R\$` quebrando moeda BR):** não — é mais específico e mais grave do
  que a hipótese original ("pode ficar imprevisível"). O bug real é determinístico (100% null ou
  crash total), tem causa raiz exata identificada (escape de string inválido, não falta de
  normalização), e ocorre em 2 lugares do código gerado de forma independente. Não encontrado em
  nenhuma entrada de `docs/CURRENT_STATE.md` nem no artifact de auditoria anterior.
- **Achado 3 (JSON aninhado derruba o Transformer):** não, e o comportamento real (falha total,
  sem código nem erro visível na UI) é diferente da hipótese original ("vira string tipo dict").
  Não documentado antes.
- **Achado 4 (tabela DOCX extraída bem; datas mistas quebram):** a extração da tabela em si já
  era um gap conhecido e corrigido (comentário no próprio `document_source.py`, confirmado
  funcionando ao vivo agora). O sub-achado de datas mistas quebrando é uma variante nova,
  relacionada mas não idêntica ao bug pt-BR já documentado em `docs/CURRENT_STATE.md`
  (sessão 2 de 2026-09-04) — aquele cobria ISO puro vs BR puro, não coluna com formatos
  misturados linha a linha. Vale confirmar se a PR #191 mencionada na tarefa cobre este caso.

## Recomendação

1. **Prioridade 1 — corrigir o bug de escape `'R\$ '` → `'R$ '`** nos dois lugares onde o padrão
   apareceu (código gerado pelo Transformer e pelo Analyst). Como é um bug no *prompt/exemplo*
   que induz o LLM a escrever `\$` (provavelmente um exemplo de few-shot ou instrução do sistema
   ensinando a "escapar" o `$` como se fosse regex, quando `str.replace` sem `regex=True` trata a
   string como literal), a correção mais robusta é ajustar o prompt do Transformer/Analyst para
   não sugerir escapar `$` em `str.replace` literal — ou instruir explicitamente para usar
   `regex=True` com um padrão correto (`r'R\$\s*'`) se for esse o caminho. Vale adicionar um teste
   de regressão com um valor `"R$ 1.234,56"` de exemplo real.
2. **Prioridade 2 — expor `sheet_name` no schema do plano do Orchestrator e no formulário Run**
   para Excel multi-aba, ou pelo menos detectar o caso e devolver um erro amigável orientando o
   usuário ("este Excel tem N abas, escolha uma") em vez de um `ValueError` técnico.
3. **Prioridade 3 — decidir o que fazer com JSON genuinamente aninhado**: hoje não há nenhum
   caminho de sucesso. Ou documentar a limitação de forma explícita no formulário ("JSON deve ser
   uma lista plana de registros"), ou investir em achatamento automático (`pandas.json_normalize`)
   antes de mandar pro Transformer.
4. **Prioridade 4 — revisar a lógica de parsing de data para formatos mistos dentro da mesma
   coluna.** O padrão atual (`dayfirst` vs não-`dayfirst`, escolhendo o que tiver menos NaN) não
   cobre coluna com formatos inconsistentes linha a linha porque o `pd.to_datetime` sem `format`
   explícito infere um único formato do primeiro valor e aplica a todos. Point de partida:
   comparar com a correção já aplicada em `core/locale.py`/`agents/pipeline/transformer.py`
   (sessão 2026-09-04) e verificar se a PR #191 (mencionada nesta tarefa) já cobre esse caso
   específico — se não cobrir, é um gap real a reabrir.
5. Nenhuma correção foi aplicada nesta rodada (conforme instrução) — nenhum commit, nenhuma PR,
   nenhum dado de produção alterado ou deletado. Os 4 arquivos de teste continuam em
   `/private/tmp/claude-501/.../scratchpad/testfiles/`, não commitados.
