"use client";

import { SignInButton } from "@clerk/nextjs";
import { motion } from "motion/react";
import {
  Boxes,
  Database,
  FileStack,
  GitBranch,
  Lock,
  Radar,
  ShieldCheck,
  Sparkles,
  Terminal,
  Workflow,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PYRAMID } from "@/lib/agents";

// Shared fade-up entrance, triggered once as each section scrolls into view
// — cheap, tasteful, and consistent with `agent-progress.tsx`'s existing use
// of `motion/react` elsewhere in this app (Sprint 7), not a new dependency.
const fadeUp = {
  initial: { opacity: 0, y: 16 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-80px" },
  transition: { duration: 0.5, ease: "easeOut" as const },
};

const DIFFERENTIATORS = [
  {
    icon: Database,
    title: "Multi-fonte heterogênea, não um arquivo por vez",
    body: "CSV, conexão real a Postgres/MySQL/MongoDB, REST autenticado e PDF/DOCX numa spec só — um agente decide como combinar. Não dá pra colar isso num chat.",
  },
  {
    icon: FileStack,
    title: "Pipeline auditável e persistido, não uma resposta efêmera",
    body: "Cada execução gera código real — Transformer, Analyst, Science — salvo, revisável, versionável. Sobrevive a fechar a aba; a resposta de um chat não.",
  },
  {
    icon: Workflow,
    title: "API-first, não chat-first",
    body: "O pipeline é um endpoint chamável, agendável, integrável a outro sistema — infraestrutura de dados, não uma conversa que alguém precisa lembrar de repetir toda semana.",
  },
  {
    icon: ShieldCheck,
    title: "Governança e isolamento por tenant",
    body: "Dado sensível não passa pelo chat de um terceiro sem controle: isolamento por tenant, sandbox com timeout, log de auditoria de cada ação de cada agente.",
  },
];

const TECH_FACTS = [
  { icon: GitBranch, label: "Orquestração multiagente via LangGraph, estado explícito" },
  { icon: Terminal, label: "Código gerado roda em sandbox isolado, com timeout" },
  { icon: Lock, label: "Isolamento por tenant com Row-Level Security no Postgres" },
  { icon: Radar, label: "Retry automático + monitoramento de saúde de execuções agendadas" },
  { icon: Boxes, label: "Validação de saída: resultado é conferido antes de chegar a você" },
  { icon: Sparkles, label: "Autoavaliação de prontidão SOC2 e política formal de dados (LGPD/GDPR)" },
];

export default function LandingPage() {
  return (
    <main className="flex-1">
      {/* Hero */}
      <section className="relative overflow-hidden px-6 pt-20 pb-24 sm:pt-28 sm:pb-32">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[480px] bg-[radial-gradient(ellipse_60%_50%_at_50%_0%,var(--color-primary)/12,transparent)]"
        />
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mx-auto flex max-w-3xl flex-col items-center gap-6 text-center"
        >
          <Badge variant="outline" className="gap-1.5 text-muted-foreground">
            <Sparkles className="size-3" />
            Multiagente · código auditável
          </Badge>
          <h1 className="text-balance text-4xl font-semibold tracking-tight sm:text-6xl">
            Descreva o pipeline.
            <br />
            Os agentes constroem — você audita.
          </h1>
          <p className="text-balance text-lg text-muted-foreground sm:text-xl">
            Uma especificação em linguagem natural vira extração, limpeza e carga de dados
            heterogêneos. Cada etapa gera código Python real, revisável — não uma resposta de
            chat que some quando você fecha a aba.
          </p>
          <div className="mt-2 flex flex-col items-center gap-3 sm:flex-row">
            <SignInButton forceRedirectUrl="/app">
              <Button size="lg" className="h-11 px-6 text-base">
                Entrar e rodar um pipeline
              </Button>
            </SignInButton>
            <Button
              size="lg"
              variant="ghost"
              className="h-11 px-6 text-base"
              render={<a href="#como-funciona">Ver como funciona</a>}
            />
          </div>
        </motion.div>
      </section>

      {/* Why not just a chat */}
      <section className="border-t border-border/60 px-6 py-20 sm:py-28">
        <div className="mx-auto max-w-5xl">
          <motion.div {...fadeUp} className="mx-auto mb-12 max-w-2xl text-center">
            <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              &ldquo;Por que não subo a planilha num chat e pergunto?&rdquo;
            </h2>
            <p className="mt-3 text-muted-foreground">
              Pergunta justa. Pra um arquivo isolado e uma pergunta pontual, um Code Interpreter
              genérico já resolve — e é grátis. O AI-ETL existe pra onde isso para de funcionar.
            </p>
          </motion.div>
          <div className="grid gap-4 sm:grid-cols-2">
            {DIFFERENTIATORS.map((item, i) => (
              <motion.div
                key={item.title}
                {...fadeUp}
                transition={{ ...fadeUp.transition, delay: i * 0.06 }}
              >
                <Card className="h-full">
                  <CardHeader>
                    <div className="mb-1 flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <item.icon className="size-4.5" />
                    </div>
                    <CardTitle className="text-base">{item.title}</CardTitle>
                  </CardHeader>
                  <CardContent className="text-sm text-muted-foreground">{item.body}</CardContent>
                </Card>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="como-funciona" className="border-t border-border/60 px-6 py-20 sm:py-28">
        <div className="mx-auto max-w-5xl">
          <motion.div {...fadeUp} className="mx-auto mb-14 max-w-2xl text-center">
            <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              Do dado bruto à recomendação, uma pirâmide de agentes
            </h2>
            <p className="mt-3 text-muted-foreground">
              Nove agentes especializados, cada um com uma responsabilidade só — o estado do
              pipeline passa de um pro outro, nunca escondido numa caixa-preta.
            </p>
          </motion.div>
          <motion.div
            {...fadeUp}
            className="flex flex-col gap-3 sm:flex-row sm:items-stretch sm:gap-2"
          >
            {PYRAMID.map((tier, i) => (
              <div key={tier.label} className="flex flex-1 items-center gap-2 sm:contents">
                <Card className="w-full flex-1 py-5 text-center">
                  <CardContent className="flex flex-col items-center gap-1.5 px-3">
                    <span className="text-2xl">{tier.emoji}</span>
                    <span className="text-sm font-medium">{tier.label}</span>
                    <span className="text-xs text-muted-foreground">{tier.description}</span>
                  </CardContent>
                </Card>
                {i < PYRAMID.length - 1 && (
                  <span
                    aria-hidden
                    className="hidden text-muted-foreground/40 sm:block sm:shrink-0"
                  >
                    →
                  </span>
                )}
              </div>
            ))}
          </motion.div>
        </div>
      </section>

      {/* Dual audience */}
      <section className="border-t border-border/60 px-6 py-20 sm:py-28">
        <div className="mx-auto max-w-5xl">
          <motion.div {...fadeUp} className="mx-auto mb-12 max-w-2xl text-center">
            <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              Feito pra duas pessoas na mesma empresa
            </h2>
          </motion.div>
          <div className="grid gap-4 sm:grid-cols-2">
            <motion.div {...fadeUp}>
              <Card className="h-full">
                <CardHeader>
                  <Badge variant="secondary" className="mb-2 w-fit">
                    Operador técnico
                  </Badge>
                  <CardTitle>Quem hoje monta o relatório na mão</CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">
                  Configura a fonte, escreve a spec, audita o código gerado antes de confiar nele.
                  Sabe ler Python/pandas — não precisa escrever do zero toda semana.
                </CardContent>
              </Card>
            </motion.div>
            <motion.div {...fadeUp} transition={{ ...fadeUp.transition, delay: 0.08 }}>
              <Card className="h-full">
                <CardHeader>
                  <Badge variant="secondary" className="mb-2 w-fit">
                    Consumidor executivo
                  </Badge>
                  <CardTitle>Quem recebe o resultado, não configura nada</CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">
                  Quer a resposta e a recomendação, sem virar refém de um número que a IA pode ter
                  inventado — porque alguém do time já pôde auditar de onde ele veio.
                </CardContent>
              </Card>
            </motion.div>
          </div>
        </div>
      </section>

      {/* Tech credibility */}
      <section className="border-t border-border/60 px-6 py-20 sm:py-28">
        <div className="mx-auto max-w-4xl">
          <motion.div {...fadeUp} className="mx-auto mb-12 max-w-2xl text-center">
            <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              Construído pra produção, não só pra demo
            </h2>
          </motion.div>
          <motion.ul
            {...fadeUp}
            className="grid gap-x-8 gap-y-4 text-sm text-muted-foreground sm:grid-cols-2"
          >
            {TECH_FACTS.map((fact) => (
              <li key={fact.label} className="flex items-start gap-3">
                <fact.icon className="mt-0.5 size-4 shrink-0 text-primary" />
                <span>{fact.label}</span>
              </li>
            ))}
          </motion.ul>
        </div>
      </section>

      {/* Final CTA */}
      <section className="border-t border-border/60 px-6 py-20 sm:py-28">
        <motion.div
          {...fadeUp}
          className="mx-auto flex max-w-2xl flex-col items-center gap-6 text-center"
        >
          <h2 className="text-balance text-2xl font-semibold tracking-tight sm:text-3xl">
            Pare de reconciliar fontes à mão toda semana
          </h2>
          <SignInButton forceRedirectUrl="/app">
            <Button size="lg" className="h-11 px-8 text-base">
              Entrar e rodar um pipeline
            </Button>
          </SignInButton>
        </motion.div>
      </section>
    </main>
  );
}
