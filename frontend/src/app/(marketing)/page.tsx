"use client";

import { SignInButton } from "@clerk/nextjs";
import { motion, useReducedMotion } from "motion/react";
import {
  ArrowRight,
  Check,
  Gauge,
  GitBranch,
  Lock,
  Radar,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AGENTS } from "@/lib/agents";
import { cn } from "@/lib/utils";

/**
 * Public landing page, redesigned 2026-10-16: an editorial layout (display serif headings,
 * warm paper palette, one amber accent) in place of the earlier centred card grid. Every
 * claim on the page is a fact of the artefact: the stats and the FAQ match the code and the
 * case-study evidence; the demo run and the mock panels are labelled as examples.
 */

const fadeUp = {
  initial: { opacity: 0, y: 14 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-80px" },
  transition: { duration: 0.5, ease: "easeOut" as const },
};

const TECH_FACT_ICONS = [GitBranch, Terminal, Lock, Radar, ShieldCheck, Gauge];

// Generated-code sample for the audit section: the shape of what the Transformer writes.
const CODE_SAMPLE = `def transform(dfs):
    trips = dfs["trips"].drop_duplicates()
    zones = dfs["zones"]
    df = trips.merge(
        zones, left_on="PULocationID", right_on="LocationID"
    )
    return df.groupby("Borough", as_index=False).agg(
        trips=("fare_amount", "size"),
        revenue=("fare_amount", "sum"),
    )`;

// Real action names of the ETL graph's audit log (`log_action` in agents/pipeline/).
const TRAIL = [
  { time: "00:01.9", agent: "orchestrator", action: "plan_created" },
  { time: "00:02.4", agent: "extractor", action: "extraction_complete" },
  { time: "00:05.8", agent: "transformer", action: "code_executed" },
  { time: "00:05.9", agent: "quality", action: "checks_complete" },
  { time: "00:06.3", agent: "loader", action: "load_complete" },
];

type Step = { agent: string; detail: string };
type Item = { title: string; body: string };
type Stat = { value: string; label: string };
type QualityRow = { label: string; value: string; state: "ok" | "warn" };
type Faq = { q: string; a: string };
type RosterText = { description: string };

function Eyebrow({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p
      className={cn(
        "flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground",
        className,
      )}
    >
      <span aria-hidden className="size-1.5 rounded-full bg-brand" />
      {children}
    </p>
  );
}

function SectionTitle({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <h2
      className={cn(
        "font-serif text-4xl leading-[1.05] tracking-tight text-balance sm:text-5xl",
        className,
      )}
    >
      {children}
    </h2>
  );
}

function PipelineDemo() {
  const t = useTranslations("landing");
  const steps = t.raw("demo.steps") as Step[];
  const reduceMotion = useReducedMotion();
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (reduceMotion) return;
    const id = setInterval(() => setTick((n) => (n + 1) % (steps.length + 4)), 1100);
    return () => clearInterval(id);
  }, [reduceMotion, steps.length]);

  const active = reduceMotion ? steps.length : tick;
  const finished = active >= steps.length;

  return (
    <div className="relative rounded-lg border border-border bg-card shadow-[0_1px_0_rgb(0_0_0/0.03),0_30px_60px_-30px_rgb(0_0_0/0.35)]">
      <div className="flex items-center justify-between border-b border-border px-5 py-3">
        <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
          {t("demo.label")}
        </span>
        <span className="flex gap-1.5" aria-hidden>
          {steps.map((step, i) => (
            <span
              key={step.agent}
              className={cn(
                "h-1 w-5 rounded-full transition-colors duration-500",
                i < active ? "bg-brand" : "bg-border",
              )}
            />
          ))}
        </span>
      </div>
      <div className="px-5 pt-4">
        <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          {t("demo.specLabel")}
        </p>
        <p className="mt-2 rounded-md bg-muted px-3 py-2.5 font-mono text-[13px] leading-relaxed">
          <span aria-hidden className="mr-1.5 text-brand-strong">
            ›
          </span>
          {t("demo.spec")}
        </p>
      </div>
      <ol className="flex flex-col gap-1 px-3 py-4">
        {steps.map((step, i) => {
          const done = i < active;
          const running = i === active;
          return (
            <li
              key={step.agent}
              className={cn(
                "grid grid-cols-[20px_104px_1fr] items-center gap-3 rounded-md px-2 py-2 transition-colors duration-500",
                running && "bg-accent",
              )}
            >
              <span className="flex justify-center" aria-hidden>
                {done ? (
                  <Check className="size-3.5 text-brand-strong" />
                ) : running ? (
                  <span className="size-2 animate-pulse rounded-full bg-brand" />
                ) : (
                  <span className="size-2 rounded-full border border-muted-foreground/40" />
                )}
              </span>
              <span
                className={cn(
                  "font-mono text-[13px]",
                  done || running ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {step.agent}
              </span>
              <span
                className={cn(
                  "truncate text-sm text-muted-foreground transition-opacity duration-500",
                  done || running ? "opacity-100" : "opacity-40",
                )}
              >
                {step.detail}
              </span>
            </li>
          );
        })}
      </ol>
      <div
        className={cn(
          "flex items-center gap-2 border-t border-border px-5 py-3 text-sm transition-opacity duration-500",
          finished ? "opacity-100" : "opacity-0",
        )}
      >
        <ArrowRight className="size-3.5 text-brand-strong" aria-hidden />
        {t("demo.result")}
      </div>
    </div>
  );
}

function EtlGraph() {
  const t = useTranslations("landing");
  const nodes = ["Orchestrator", "Extractor", "Transformer", "Quality", "Loader"];
  return (
    <div className="flex flex-col items-start font-mono text-[13px]">
      {nodes.map((node, i) => (
        <div key={node} className="flex flex-col items-start">
          <div className="flex flex-wrap items-center gap-3">
            <span
              className={cn(
                "rounded-md border px-3 py-1.5",
                node === "Quality"
                  ? "border-brand/60 bg-brand/10 text-foreground"
                  : "border-border bg-card",
              )}
            >
              {node}
              {node === "Quality" && (
                <span className="ml-2 text-[11px] text-brand-strong">{t("graphGate")}</span>
              )}
            </span>
            {node === "Quality" && (
              <span className="flex items-center gap-2 text-[12px] text-muted-foreground">
                <span aria-hidden className="h-px w-6 bg-border" />
                {t("graphBlocked")}
              </span>
            )}
          </div>
          {i < nodes.length - 1 && <span aria-hidden className="ml-6 h-4 w-px bg-border" />}
        </div>
      ))}
    </div>
  );
}

function AnalysisGraph() {
  const box = "rounded-md border border-border bg-card px-3 py-1.5";
  return (
    <div className="flex flex-col items-start font-mono text-[13px]">
      <span className={box}>Planner</span>
      <span aria-hidden className="ml-6 h-4 w-px bg-border" />
      <div className="flex gap-2">
        <span className={box}>Analyst</span>
        <span className={box}>Science</span>
      </div>
      <span aria-hidden className="ml-6 h-4 w-px bg-border" />
      <span className={cn(box, "border-brand/60 bg-brand/10")}>Advisor</span>
    </div>
  );
}

function AgentList({ from, to }: { from: number; to: number }) {
  const tAgents = useTranslations("agents");
  const roster = tAgents.raw("roster") as RosterText[];
  return (
    <ol className="flex flex-col">
      {AGENTS.slice(from, to).map((agent, i) => (
        <li
          key={agent.id}
          className="grid grid-cols-[36px_1fr] gap-3 border-t border-border py-4 first:border-t-0"
        >
          <span className="font-mono text-xs text-muted-foreground">
            {String(from + i + 1).padStart(2, "0")}
          </span>
          <div>
            <p className="font-medium">{agent.name}</p>
            <p className="mt-1 text-sm text-muted-foreground">{roster[from + i].description}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

function AuditCard({
  item,
  delay,
  children,
}: {
  item: Item;
  delay: number;
  children: React.ReactNode;
}) {
  return (
    <motion.article
      {...fadeUp}
      transition={{ ...fadeUp.transition, delay }}
      className="flex flex-col overflow-hidden rounded-lg border border-border bg-card"
    >
      {children}
      <div className="flex flex-col gap-2 border-t border-border p-6">
        <h3 className="text-lg font-medium tracking-tight">{item.title}</h3>
        <p className="text-sm text-muted-foreground">{item.body}</p>
      </div>
    </motion.article>
  );
}

export default function LandingPage() {
  const t = useTranslations("landing");
  const stats = t.raw("stats") as Stat[];
  const differentiators = t.raw("differentiators") as Item[];
  const auditCards = t.raw("auditCards") as Item[];
  const qualityRows = t.raw("qualityRows") as QualityRow[];
  const techFacts = t.raw("techFacts") as string[];
  const faq = t.raw("faq") as Faq[];

  return (
    <main className="flex-1">
      {/* Hero */}
      <section className="relative overflow-hidden border-b border-border">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(var(--border)_1px,transparent_1px)] [background-size:22px_22px] [mask-image:radial-gradient(ellipse_70%_60%_at_30%_40%,black,transparent)]"
        />
        <div className="mx-auto grid max-w-6xl items-center gap-14 px-6 pt-16 pb-20 sm:pt-24 sm:pb-28 lg:grid-cols-[1.05fr_1fr]">
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.55 }}
            className="flex flex-col gap-7"
          >
            <Eyebrow>{t("eyebrow")}</Eyebrow>
            <h1 className="font-serif text-5xl leading-[1.02] tracking-tight text-balance sm:text-7xl">
              {t("heroTitle1")}
              <br />
              <em className="text-brand-strong">{t("heroTitle2")}</em>
            </h1>
            <p className="max-w-xl text-lg leading-relaxed text-muted-foreground">
              {t("heroSubtitle")}
            </p>
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
              <SignInButton forceRedirectUrl="/app">
                <Button size="lg" className="h-11 gap-2 px-5 text-[15px]">
                  {t("ctaPrimary")}
                  <ArrowRight className="size-4" aria-hidden />
                </Button>
              </SignInButton>
              <a
                href="#how-it-works"
                className="text-[15px] text-muted-foreground underline decoration-border underline-offset-[6px] transition-colors hover:text-foreground hover:decoration-brand"
              >
                {t("ctaSecondary")}
              </a>
            </div>
          </motion.div>
          <motion.div
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.1 }}
          >
            <PipelineDemo />
          </motion.div>
        </div>
      </section>

      {/* Stats */}
      <section className="border-b border-border">
        <dl className="mx-auto grid max-w-6xl grid-cols-2 lg:grid-cols-4">
          {stats.map((stat, i) => (
            <div
              key={stat.label}
              className={cn(
                "flex flex-col gap-2 px-6 py-10",
                i % 2 === 1 && "border-l border-border",
                i === 2 && "lg:border-l",
                i >= 2 && "border-t border-border lg:border-t-0",
              )}
            >
              <dt className="order-2 text-sm text-muted-foreground">{stat.label}</dt>
              <dd className="order-1 font-serif text-5xl tracking-tight">{stat.value}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* Why not a chat */}
      <section className="border-b border-border">
        <div className="mx-auto grid max-w-6xl gap-12 px-6 py-24 sm:py-32 lg:grid-cols-[1fr_1.15fr]">
          <motion.div {...fadeUp} className="flex flex-col gap-5 lg:sticky lg:top-28 lg:self-start">
            <Eyebrow>{t("whyEyebrow")}</Eyebrow>
            <SectionTitle>{t("whyTitle")}</SectionTitle>
            <p className="max-w-md text-muted-foreground">{t("whyBody")}</p>
          </motion.div>
          <ol className="flex flex-col">
            {differentiators.map((item, i) => (
              <motion.li
                key={item.title}
                {...fadeUp}
                transition={{ ...fadeUp.transition, delay: i * 0.05 }}
                className="grid grid-cols-[48px_1fr] gap-4 border-t border-border py-7 last:border-b"
              >
                <span className="font-mono text-xs text-brand-strong">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div>
                  <h3 className="text-lg font-medium tracking-tight">{item.title}</h3>
                  <p className="mt-2 text-muted-foreground">{item.body}</p>
                </div>
              </motion.li>
            ))}
          </ol>
        </div>
      </section>

      {/* How it works */}
      <section id="how-it-works" className="scroll-mt-20 border-b border-border bg-secondary/40">
        <div className="mx-auto max-w-6xl px-6 py-24 sm:py-32">
          <motion.div {...fadeUp} className="flex max-w-2xl flex-col gap-5">
            <Eyebrow>{t("howEyebrow")}</Eyebrow>
            <SectionTitle>{t("howTitle")}</SectionTitle>
            <p className="text-muted-foreground">{t("howBody")}</p>
          </motion.div>
          <Tabs defaultValue="etl" className="mt-12 gap-8">
            <TabsList variant="line" className="h-auto border-b border-border p-0">
              <TabsTrigger value="etl" className="px-4 pb-3 text-[15px]">
                {t("howTabEtl")}
              </TabsTrigger>
              <TabsTrigger value="analysis" className="px-4 pb-3 text-[15px]">
                {t("howTabAnalysis")}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="etl">
              <div className="grid gap-10 lg:grid-cols-[1.1fr_1fr]">
                <AgentList from={0} to={5} />
                <div className="flex flex-col gap-6 rounded-lg border border-border bg-card p-6">
                  <EtlGraph />
                  <p className="border-t border-border pt-5 text-sm text-muted-foreground">
                    {t("howEtlNote")}
                  </p>
                </div>
              </div>
            </TabsContent>
            <TabsContent value="analysis">
              <div className="grid gap-10 lg:grid-cols-[1.1fr_1fr]">
                <AgentList from={5} to={9} />
                <div className="flex flex-col gap-6 rounded-lg border border-border bg-card p-6">
                  <AnalysisGraph />
                  <p className="border-t border-border pt-5 text-sm text-muted-foreground">
                    {t("howAnalysisNote")}
                  </p>
                </div>
              </div>
            </TabsContent>
          </Tabs>
        </div>
      </section>

      {/* What you audit */}
      <section id="audit" className="scroll-mt-20 border-b border-border">
        <div className="mx-auto max-w-6xl px-6 py-24 sm:py-32">
          <motion.div {...fadeUp} className="flex max-w-2xl flex-col gap-5">
            <Eyebrow>{t("auditEyebrow")}</Eyebrow>
            <SectionTitle>{t("auditTitle")}</SectionTitle>
            <p className="text-muted-foreground">{t("auditBody")}</p>
          </motion.div>
          <div className="mt-14 grid gap-5 lg:grid-cols-3">
            <AuditCard item={auditCards[0]} delay={0}>
              <pre className="h-56 overflow-hidden bg-[#0e131c] px-5 py-4 font-mono text-[12px] leading-relaxed text-[#e6e3da]">
                <code>{CODE_SAMPLE}</code>
              </pre>
            </AuditCard>
            <AuditCard item={auditCards[1]} delay={0.06}>
              <ul className="flex h-56 flex-col justify-center gap-1 bg-muted/60 px-5 py-4">
                {qualityRows.map((row) => (
                  <li
                    key={row.label}
                    className="flex items-center justify-between rounded-md bg-card px-3 py-2 text-sm"
                  >
                    <span className="flex items-center gap-2">
                      <span
                        aria-hidden
                        className={cn(
                          "size-1.5 rounded-full",
                          row.state === "ok" ? "bg-muted-foreground/50" : "bg-brand",
                        )}
                      />
                      {row.label}
                    </span>
                    <span
                      className={cn(
                        "font-mono text-[12px]",
                        row.state === "warn" ? "text-brand-strong" : "text-muted-foreground",
                      )}
                    >
                      {row.value}
                    </span>
                  </li>
                ))}
              </ul>
            </AuditCard>
            <AuditCard item={auditCards[2]} delay={0.12}>
              <ol className="flex h-56 flex-col justify-center gap-2.5 bg-muted/60 px-5 py-4 font-mono text-[12px]">
                {TRAIL.map((row) => (
                  <li key={row.action} className="grid grid-cols-[56px_88px_1fr] gap-2">
                    <span className="text-muted-foreground">{row.time}</span>
                    <span>{row.agent}</span>
                    <span className="truncate text-brand-strong">{row.action}</span>
                  </li>
                ))}
              </ol>
            </AuditCard>
          </div>
        </div>
      </section>

      {/* Audience */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-6 py-24 sm:py-32">
          <motion.div {...fadeUp}>
            <SectionTitle className="max-w-2xl">{t("dualTitle")}</SectionTitle>
          </motion.div>
          <div className="mt-12 grid gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-2">
            {(
              [
                ["dualOperatorBadge", "dualOperatorTitle", "dualOperatorBody"],
                ["dualExecBadge", "dualExecTitle", "dualExecBody"],
              ] as const
            ).map(([badge, title, body]) => (
              <motion.div key={badge} {...fadeUp} className="flex flex-col gap-4 bg-card p-8">
                <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-brand-strong">
                  {t(badge)}
                </span>
                <h3 className="font-serif text-3xl leading-tight tracking-tight">{t(title)}</h3>
                <p className="text-muted-foreground">{t(body)}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Built for production */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <motion.h2 {...fadeUp} className="font-serif text-3xl tracking-tight">
            {t("techTitle")}
          </motion.h2>
          <ul className="mt-10 grid gap-x-10 gap-y-6 sm:grid-cols-2 lg:grid-cols-3">
            {techFacts.map((fact, i) => {
              const Icon = TECH_FACT_ICONS[i];
              return (
                <li key={fact} className="flex items-start gap-3 text-sm">
                  <Icon className="mt-0.5 size-4 shrink-0 text-brand-strong" aria-hidden />
                  <span>{fact}</span>
                </li>
              );
            })}
          </ul>
        </div>
      </section>

      {/* FAQ */}
      <section id="faq" className="scroll-mt-20 border-b border-border">
        <div className="mx-auto grid max-w-6xl gap-12 px-6 py-24 sm:py-32 lg:grid-cols-[1fr_1.4fr]">
          <motion.div {...fadeUp} className="flex flex-col gap-5">
            <Eyebrow>{t("faqEyebrow")}</Eyebrow>
            <SectionTitle>{t("faqTitle")}</SectionTitle>
          </motion.div>
          <div className="flex flex-col">
            {faq.map((item, i) => (
              <details
                key={item.q}
                className="group border-t border-border py-5 last:border-b [&_summary::-webkit-details-marker]:hidden"
              >
                <summary className="grid cursor-pointer list-none grid-cols-[40px_1fr_20px] items-baseline gap-3">
                  <span className="font-mono text-xs text-muted-foreground">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="text-lg tracking-tight">{item.q}</span>
                  <span
                    aria-hidden
                    className="text-muted-foreground transition-transform group-open:rotate-45"
                  >
                    +
                  </span>
                </summary>
                <p className="mt-3 pl-[52px] text-muted-foreground">{item.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* Final call to action */}
      <section className="bg-[#080c14] text-[#ecebe6] dark:bg-card">
        <motion.div
          {...fadeUp}
          className="mx-auto flex max-w-6xl flex-col items-start gap-8 px-6 py-24 sm:py-28 lg:flex-row lg:items-end lg:justify-between"
        >
          <div className="flex max-w-2xl flex-col gap-4">
            <h2 className="font-serif text-4xl leading-[1.05] tracking-tight text-balance sm:text-6xl">
              {t("finalCtaTitle")}
            </h2>
            <p className="text-lg text-[#ecebe6]/70">{t("finalCtaBody")}</p>
          </div>
          <SignInButton forceRedirectUrl="/app">
            <Button
              size="lg"
              className="h-11 gap-2 bg-brand px-5 text-[15px] text-[#1d1c19] hover:bg-brand/90"
            >
              {t("ctaPrimary")}
              <ArrowRight className="size-4" aria-hidden />
            </Button>
          </SignInButton>
        </motion.div>
      </section>
    </main>
  );
}
