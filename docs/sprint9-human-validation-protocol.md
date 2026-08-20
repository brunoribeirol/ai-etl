# Sprint 9 — Human Validation Study Protocol

> **Status:** protocol design only. No live sessions were run to produce this document — see
> "What this document does not cover" below. Ready for the project owner to execute manually.
>
> **Author of this protocol:** drafted by an automated agent (Claude) under Sprint 9 scope, from
> `artefact/sprint-roadmap.md` (Sprint 9) and `artefact/evaluation-metrics.md` (Metric 9) in the
> Obsidian vault. Reviewed and to be executed by Bruno Ribeiro.
>
> **Language note:** this document follows the project's English-only documentation convention,
> with one deliberate exception — Section 9 (Informed Consent Form) is written in Portuguese
> because it is a participant-facing legal instrument for LGPD-conscious informed consent, and
> must be given to Brazilian participants in a language they can meaningfully consent in. An
> English gloss is provided alongside it for reviewers.

---

## 1. Objective

Answer Metric 9 of the evaluation framework (`artefact/evaluation-metrics.md`): **time saved and
trust in generated code**, via a moderated study with real users operating the live AI-ETL
frontend (`ai-etl.vercel.app`) unassisted, following Sprint 6/7's real Clerk login + polished UI.

This protocol also collects data usable for Metric 2 (perceived output quality, via the
participant's own judgment layered on top of the Quality agent's automated score) and produces
qualitative material for the TCC's Evaluation section (target: November 2026).

**Explicitly out of scope for this protocol:** metrics 1, 3, 4, 5, 6, 7, 8, 10 — those are
already instrumented or measured elsewhere in the pipeline (audit trail, cost tracking, stage
latencies, sandbox tests) and do not need a human study.

---

## 2. Why this needs a human study (not just automated metrics)

The pipeline's own audit trail can tell you a run succeeded, how long it took, and what it cost.
It cannot tell you:

- Whether a real user with a real business question, given the raw output, would **trust** the
  generated Python code enough to run it against production data (auditability metric — this
  project's whole pitch versus "just paste it into ChatGPT").
- Whether the time the pipeline took felt **faster than the user's own alternative** — spreadsheet
  formulas, a `pandas` script, a BI tool — for their specific skill level.
- Whether the natural-language flow is usable **without an engineer standing behind the user**,
  which is the entire premise of Sprint 6's real-auth frontend replacing the old pasted-token
  Streamlit workaround.

These require a moderated or semi-moderated session with a real person operating the product.

---

## 3. Participant profile & recruitment

The project has two unreconciled ICP framings (`saas-potential.md` §2 vs. §7, flagged as an open
item in `docs/CURRENT_STATE.md`). Rather than wait for that to resolve, this protocol **recruits
across both segments** and treats segment as an analysis variable — the study becomes useful
evidence for that ICP debate too, not just for the TCC.

| Segment | Definition | Why included | Target n |
|---|---|---|---|
| **A — Technical** | Comfortable writing `pandas`/SQL manually; job involves data cleaning/analysis (data analyst, data engineer, BI analyst, CS student with data coursework) | Can give a *credible* manual-time baseline (the comparison in Metric 9 is meaningless from someone who couldn't do the manual task at all) | 3–4 |
| **B — Semi-technical / business** | Comfortable with Excel/Google Sheets formulas, not with code; makes or consumes business decisions from data (SMB owner/operator, product manager, ops analyst) | Represents the "leadership consuming the result" ICP half from `saas-potential.md` §2; tests whether NL-spec removes the engineer-as-intermediary bottleneck | 3–4 |

**Total target: 6–8 participants.** This follows Nielsen's well-established usability-research
finding that 5 users surface the large majority of usability issues in a moderated qualitative
study — this is not a statistically powered quantitative study, and the write-up must say so
explicitly (see §11). A small, well-documented sample is the stated bar in the roadmap
(`sprint-roadmap.md`: "mesmo uma amostra pequena e bem documentada é mais forte que nenhuma").

**Recruitment channels (suggested, Bruno's call):**
- Personal/professional network (CESAR School classmates and faculty for Segment A; any SMB
  contacts, freelance clients, or family-business contacts for Segment B).
- Explicitly avoid recruiting anyone who has already seen the AI-ETL frontend or discussed the
  project's internals with Bruno beforehand — that biases both the time estimate and the trust
  rating. Screen for this in the intake step below.

**Exclusion criteria:**
- Anyone who contributed to this repository or reviewed its code.
- Anyone under 18 (simplifies LGPD/consent handling — no guardian consent flow needed).

**Screening question (ask before scheduling):** *"Have you used or seen AI-ETL before, in any
form?"* — exclude if yes.

---

## 4. Study design overview

**Format:** moderated, one-on-one, ~45–60 minutes, in person or via video call with screen share.
Moderated (not a self-serve async survey) because the qualitative trust/confidence signal is the
point, and think-aloud commentary during the task is itself data (§8).

**Structure per session:**

1. Intro + consent (5 min)
2. Baseline interview — current manual process (5 min)
3. Task 1: business question via AI-ETL, unassisted (10–20 min, timed)
4. Task 2: evaluate the result + generated code (10 min)
5. Post-task structured questionnaire (5–10 min)
6. Semi-structured debrief interview (10 min)

Full moderator script in §7.

---

## 5. Materials & environment needed

- A working AI-ETL account for each participant (real Clerk sign-up — Google OAuth per Sprint 7's
  live-verified flow, no shared credentials, no pasted tokens).
- A **realistic dataset the participant did not create**, so the "business question" is genuine
  and not something they already know the answer to. Recommended: reuse the case study's
  synthetic-but-realistic datasets already in the repo (`case_study/data/sales.csv`,
  `case_study/data/orders.csv` — see `case_study/data/generate_sales.py`/`generate_orders.py` for
  schema: `order_id, customer_id, dt, product, amt, quantity, status, region` for sales). These
  already have injected quality issues (nulls, duplicates, outliers) that exercise the Quality
  agent honestly — do not hand-pick a clean file, that would make the pipeline look easier than
  it is in production use.
- A screen recorder or the moderator's own note-taking template (timestamped) — see §8 for exact
  fields to capture. Recording requires explicit separate consent (§9 covers this).
- Printed or digital copy of the consent form (§9) and the post-task questionnaire (§8), or a
  Google Form / Typeform mirroring them for remote sessions.
- A stopwatch or timer visible only to the moderator (not the participant — visible timers change
  behavior and add pressure that isn't representative).

---

## 6. Task specification

Two tasks, both performed inside the live AI-ETL frontend after real sign-in.

### Task 1 — Run a pipeline against a real business question

**Prompt given to participant** (read aloud, not shown as a checklist — this mirrors how a real
user would arrive with a real question, not a script):

> "Imagine you just joined a small retail company and were handed this sales export. Your manager
> wants to know: **which product and region combination generated the most revenue last quarter,
> and are there any months where things look unusual?** Upload the file, ask the tool that
> question in your own words, and get to an answer."

- Dataset: `sales.csv` (or `orders.csv` for variety across participants — alternate to avoid
  every session using the identical file, which would make later sessions' "time saved" estimate
  contaminated by word-of-mouth).
- The participant writes their own business question in the "Executar" page's free-text field —
  **do not dictate exact wording**, that's the entire point of testing an NL interface.
  Moderator only steers back to the prompt's intent if the participant asks for clarification.
  Moderator does not touch the keyboard/mouse.
  If genuinely stuck for over 2 minutes at the same step, the moderator may give one neutral
  nudge ("what would you type to ask that?") — log this as an assist in the observation notes
  (§8), it matters for the trust/independence read.
- **Timing starts** when the participant opens the upload screen and **stops** when they've seen
  a completed result (chart/table) they consider an answer to the question — capture this as
  `t_ai_etl` in minutes.

### Task 2 — Evaluate the output and the generated code

- Participant is shown the run's result page (Silver table, Gold/Science chart, and — since
  Sprint 7 — the "Pipeline"/"Código" tabs with the real generated Python).
- **Segment A** (technical): ask them to actually read the generated transform/analysis code and
  say whether they'd trust running it unmodified against a real production dataset, and why/why
  not.
- **Segment B** (semi-technical): ask them to look at the code tab even if they can't evaluate it
  line-by-line, and rate how much the *existence* of visible, downloadable code changes their
  trust in the answer versus a black-box chat response — this tests the auditability pitch
  specifically for the audience least equipped to verify code directly.

### Baseline (asked before Task 1, not after — to avoid anchoring on the AI-ETL experience)

Before touching the tool, ask: *"If you had to answer this same question yourself, right now,
with whatever tools you normally use (Excel, SQL, a script, asking a colleague) — roughly how
long would that take you, start to finish?"* — capture as `t_manual_estimate`. This is a
self-reported estimate, not a measured manual task (running a real manual baseline per
participant would double session length and is out of scope for this sample size) — the write-up
must state this limitation plainly (§11).

---

## 7. Moderator script (verbatim structure)

```
0. BEFORE THE SESSION
   - Confirm participant has never seen AI-ETL (screening question, §3).
   - Send/print consent form (§9) ahead of time if remote, so they can read without time pressure.

1. INTRO (5 min)
   "Thanks for helping test this. This is a research study for a university thesis project,
    not a product pitch — I need your honest reactions, including anything that's confusing,
    slow, or feels untrustworthy. There's no wrong way to use it. I'll ask you to think out loud
    as you go if that's comfortable for you."
   -> Present and collect the consent form (§9). Do not proceed without a signature/checkbox.
   -> Ask: "Any questions before we start?"

2. BASELINE INTERVIEW (5 min)
   "Before you touch the tool: [read Task 1's prompt]. Roughly how long would answering that
    take you today, with whatever you'd normally use?"
   -> Record t_manual_estimate + what tool they'd normally reach for (free text).

3. TASK 1 (10-20 min, timed, think-aloud)
   "Go ahead and try to answer that question using this tool. I'll be quiet and just take notes
    — talk through what you're doing/thinking as you go, and let me know when you feel you have
    an answer."
   -> Start timer on upload-screen open. Take observation notes (§8). One neutral nudge max if
      stuck >2 min at the same step (log it). Stop timer when participant considers task done.

4. TASK 2 (10 min)
   -> Show/confirm they're on the run-detail page with Pipeline/Código tabs visible.
   -> Segment-specific prompt from §6, Task 2.
   -> Follow-up: "Would you act on this answer for a real business decision? What would make you
      more or less confident?"

5. POST-TASK QUESTIONNAIRE (5-10 min)
   -> Hand over the structured questionnaire (§8) — self-administered, moderator available for
      clarifying questions about wording only, not steering answers.

6. DEBRIEF INTERVIEW (10 min, semi-structured — see prompts in §8)

7. CLOSE
   "That's everything — thank you. Do you have any other feedback, positive or negative, that we
    didn't cover?"
   -> Thank participant, confirm how/whether they'll see results (per consent form terms).
```

---

## 8. Metrics & data collection instruments

### 8.1 Quantitative — structured post-task questionnaire

All Likert items 1–5 (1 = strongly disagree, 5 = strongly agree), collected per participant per
session. Suggested form layout (mirror as a Google Form for remote sessions):

| # | Field | Type | Metric mapped |
|---|---|---|---|
| 1 | `t_manual_estimate` (minutes) | numeric, self-report | Time saved (baseline) |
| 2 | `t_ai_etl` (minutes) | numeric, moderator-timed | Time saved (actual) |
| 3 | "The answer I got matched what I was actually asking." | Likert | Perceived usefulness |
| 4 | "I understood the natural-language question field without help." | Likert | Usability |
| 5 | "I would trust this result enough to share it with a manager/stakeholder." | Likert | Confidence in output |
| 6 | "I looked at (or tried to look at) the generated code." | yes/no | Auditability engagement |
| 7 | "Seeing the generated code increased my trust in the result." | Likert (n/a allowed) | Confidence/auditability |
| 8 | "I would use this again for a similar question." | Likert | Perceived usefulness / retention signal |
| 9 | "How many times did you need help/a hint from the moderator?" | integer (moderator-filled) | Independence / usability |
| 10 | Segment (A/B) | categorical | Analysis variable |

Compute per participant: `time_saved = t_manual_estimate - t_ai_etl` (can be negative — report
honestly if the tool was slower for some participants, especially first-time sign-up overhead).

### 8.2 Qualitative — moderator observation notes (during Task 1/2)

Timestamped free-text log, one row per notable event:

```
[mm:ss] Event: (e.g., "hesitated at upload button", "re-read the question field twice",
                "said 'oh nice' when chart appeared", "asked what 'Silver'/'Gold' meant")
```

Capture verbatim quotes wherever possible — these are the strongest material for the TCC's
qualitative Evaluation section, more so than the Likert scores.

### 8.3 Qualitative — semi-structured debrief interview prompts

Use as a starting point, follow genuine threads the participant raises rather than reading these
as a rigid script:

1. "Walk me through what was going through your head when you first saw the results page."
2. "Was there any point where you didn't trust what the tool was doing? What was it?"
3. "If a colleague asked you whether they should use this, what would you tell them?"
4. "What's missing that would make you trust this more?"
5. (Segment A only) "Would you have written that code differently? Anything that looks wrong or
   risky to you?"
6. (Segment B only) "Did having the code visible matter to you at all, even though you didn't
   read it line by line?"
7. "Anything that felt slower or more confusing than doing this the way you normally would?"

---

## 9. Informed Consent Form (LGPD) — Portuguese original + English gloss

> Written in Portuguese for participant use, per the language note at the top of this document.
> An English summary follows for reviewers who don't read Portuguese.

### 9.1 Termo de Consentimento Livre e Esclarecido (TCLE)

```
TERMO DE CONSENTIMENTO LIVRE E ESCLARECIDO
Estudo: Validação de usuário — AI-ETL (Trabalho de Conclusão de Curso)

Pesquisador responsável: Bruno Ribeiro
Instituição: CESAR School — Ciência da Computação
Contato: araujoribeiro.bruno@gmail.com

1. SOBRE O ESTUDO
Você está sendo convidado(a) a participar de um estudo de validação de usuário para um Trabalho
de Conclusão de Curso (TCC) sobre um framework de automação de pipelines de dados baseado em
inteligência artificial (AI-ETL). O objetivo é observar como pessoas reais usam a ferramenta para
responder a uma pergunta de negócio, e coletar sua percepção sobre tempo economizado, utilidade
do resultado e confiança no código gerado.

2. O QUE VOCÊ SERÁ CONVIDADO(A) A FAZER
- Responder brevemente sobre como você resolveria uma tarefa hoje, sem a ferramenta.
- Usar a ferramenta AI-ETL (fazendo login com sua própria conta Google, via Clerk) para enviar um
  arquivo de dados e fazer uma pergunta de negócio em linguagem natural.
- Avaliar o resultado obtido e, se aplicável, o código gerado.
- Responder a um questionário curto e a algumas perguntas em conversa.
- Duração total estimada: 45 a 60 minutos.

3. DADOS COLETADOS
- Respostas ao questionário e à entrevista (texto).
- Tempo cronometrado de execução da tarefa.
- Anotações do pesquisador durante a observação da sessão.
- [SE APLICÁVEL, marcar com o participante antes de gravar] Gravação de tela e/ou áudio da
  sessão, usada apenas para revisão posterior pelo pesquisador — não será publicada nem
  compartilhada fora do TCC sem autorização adicional específica.
- Dado de uso real inserido na ferramenta durante o teste é o mesmo dataset de exemplo fornecido
  pelo pesquisador (dados sintéticos do estudo de caso do próprio TCC) — você não precisa enviar
  nenhum dado pessoal ou de sua empresa para participar.

4. USO E CONFIDENCIALIDADE DOS DADOS (LGPD — Lei 13.709/2018)
- A base legal para o tratamento dos seus dados é o seu consentimento livre e esclarecido
  (Art. 7º, I, LGPD), que você pode revogar a qualquer momento, sem qualquer prejuízo.
- Seus dados serão utilizados exclusivamente para fins acadêmicos, na elaboração do TCC e em
  eventuais publicações derivadas dele.
- Nos relatórios e no texto do TCC, suas respostas serão apresentadas de forma anonimizada
  (ex.: "Participante A", "Participante B") — seu nome não será associado publicamente às suas
  respostas ou observações.
- Seus dados de identificação (nome, e-mail/contato) serão mantidos separados das respostas
  coletadas, em arquivo de acesso restrito ao pesquisador, e usados apenas para fins de contato
  e organização logística do estudo.
- Os dados serão retidos até a conclusão e defesa do TCC (previsão: dezembro de 2026) e por até
  12 meses após, para eventual necessidade de revisão acadêmica, sendo então descartados.
  Gravações de tela/áudio, se houver, serão descartadas assim que a análise for concluída, o
  mais tardar na mesma data.
- Você pode solicitar, a qualquer momento, acesso aos seus dados, correção, anonimização
  antecipada ou exclusão, entrando em contato pelo e-mail acima.

5. RISCOS E BENEFÍCIOS
- Riscos: mínimos. Não há coleta de dados sensíveis, financeiros ou de sua empresa. O maior risco
  é o desconforto de ter seu processo de trabalho observado — você pode pausar ou encerrar a
  qualquer momento, sem necessidade de justificativa.
- Benefícios: não há remuneração ou benefício direto garantido. Sua participação contribui para
  uma pesquisa acadêmica e, indiretamente, para o desenvolvimento de uma ferramenta de código
  auditável para automação de dados.

6. VOLUNTARIEDADE
Sua participação é voluntária. Você pode se recusar a responder qualquer pergunta, pausar ou
encerrar sua participação a qualquer momento, sem qualquer penalidade ou necessidade de
justificativa.

7. CONSENTIMENTO
Declaro que li e compreendi as informações acima, que fui esclarecido(a) sobre meus direitos e
que aceito participar voluntariamente deste estudo.

(   ) Aceito participar do estudo.
(   ) Aceito, adicionalmente, que a sessão seja gravada (tela e/ou áudio) apenas para revisão
      posterior do pesquisador, conforme descrito no item 3.

Nome do(a) participante: _______________________________
Assinatura / confirmação: ______________________________
Data: ___/___/______
```

### 9.2 English gloss (for reviewers, not for participant use)

Standard informed-consent structure: study purpose (academic thesis user-validation study),
what participation involves (~45–60 min: baseline interview, task using AI-ETL with the
participant's own account, evaluation of output/code, questionnaire, debrief interview), what
data is collected (questionnaire/interview text, timed task duration, moderator notes, optional
screen/audio recording — separately opt-in, never published outside the thesis without further
specific authorization), LGPD legal basis (Art. 7, I — freely given consent, revocable anytime
without penalty), anonymization commitment (participants referred to as "Participant A/B/..." in
any written output; identifying info stored separately, access restricted to the researcher),
retention window (through thesis defense, ~Dec 2026, plus up to 12 months for academic review,
then discarded; recordings discarded as soon as analysis is done), data-subject rights (access,
correction, early anonymization, deletion — via the researcher's email), risk/benefit disclosure
(minimal risk, no sensitive/financial/company data collected, no guaranteed compensation),
voluntariness (may skip questions, pause, or withdraw anytime without justification), and a
signature block with a separate opt-in checkbox for recording consent.

---

## 10. Data handling & anonymization plan

- Raw questionnaire/interview data stored locally (not in this git repository — never commit
  participant data, even anonymized, to a public repo; keep it in a private local folder or the
  Obsidian vault's non-synced area, per the consent form's retention terms).
- Assign each participant a code (`P-A1`, `P-A2`, ... for Segment A; `P-B1`, `P-B2`, ... for
  Segment B) at intake. All analysis artifacts (spreadsheets, quotes used in the thesis) use the
  code only. The name↔code mapping lives in a separate file, access-restricted, deleted per the
  consent form's retention window.
- Screen/audio recordings (if consented) are for the researcher's own review only, deleted once
  coding/analysis is finished — do not retain "just in case."
- If a quote is striking enough to want verbatim in the thesis, get it approved against the
  anonymization commitment (no identifying detail beyond "Participant A, [segment]") before
  using it.

---

## 11. Analysis plan

### Quantitative
- Descriptive statistics only (mean/median/range) given the small, non-random sample —
  **explicitly do not report p-values or claim statistical significance**; state the sample size
  and its limits plainly in the thesis text.
- `time_saved` per participant, reported per segment (A vs. B) separately — do not pool, the two
  segments have structurally different manual baselines.
- Likert items summarized as distributions (e.g., "5/7 participants rated trust ≥4/5"), not
  averaged into a single misleading composite score.

### Qualitative
- Thematic coding of debrief interview transcripts + observation notes: read through all
  sessions, tag recurring themes (e.g., "confused by NL question field," "code visibility
  increased trust," "chart interpretation needed help"), group into a small set of named themes,
  and report theme frequency with representative anonymized quotes.
- Explicitly report disconfirming/negative feedback, not just the positive — a validation study
  that only reports success is not credible in a TCC evaluation section.

### Output for the thesis
Produce a short synthesis (1–2 pages) combining: sample description (n, segment split, screening
criteria), quantitative summary table, 3–5 named qualitative themes with quotes, and an honest
limitations paragraph (self-reported manual baseline, small non-random sample, single dataset
type, moderator presence effect). This synthesis is what feeds directly into the TCC's Evaluation
section for Metric 9.

---

## 12. What this document does not cover (explicitly out of scope for this Sprint 9 pass)

This protocol was produced by an automated agent without access to recruit or interact with real
people. It deliberately stops at a **ready-to-run protocol**, not results. Still to be done by
Bruno, manually:

- Actual recruitment of 6–8 participants across both segments.
- Running the sessions (in person or via video call).
- Transcribing/coding the qualitative data and computing the quantitative summary.
- Writing the synthesis described in §11 and folding it into the TCC's Evaluation section.
- Deciding on and executing the consent form's operational details this protocol leaves to
  judgment call (paper vs. digital signature, exact storage location for participant data,
  whether to record at all given the extra consent/deletion overhead — recording is optional in
  this protocol precisely so a lower-friction paper-only version is available if preferred).

## Related

- Vault: `artefact/evaluation-metrics.md` — Metric 9 definition this protocol answers.
- Vault: `artefact/sprint-roadmap.md` — Sprint 9 scope and dependency on Sprint 6/7.
- Vault: `artefact/saas-potential.md` §2, §7 — the two ICP framings this protocol samples across.
- `docs/CURRENT_STATE.md` — current frontend/deploy state participants will actually use.
- `case_study/data/generate_sales.py`, `generate_orders.py` — schema and injected quality issues
  for the recommended task datasets.
