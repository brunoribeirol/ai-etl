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
import { useTranslations } from "next-intl";
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

// Sprint 25 (ADR-036): translated title/body/label text now lives in
// `messages/{locale}.json` under `landing.differentiators`/`landing.techFacts`
// (arrays, read via `t.raw`) — these two arrays keep only the icon, correlated
// by index with the translated array.
const DIFFERENTIATOR_ICONS = [Database, FileStack, Workflow, ShieldCheck];
const TECH_FACT_ICONS = [GitBranch, Terminal, Lock, Radar, Boxes, Sparkles];

type Differentiator = { title: string; body: string };

export default function LandingPage() {
  const t = useTranslations("landing");
  const differentiators = t.raw("differentiators") as Differentiator[];
  const techFacts = t.raw("techFacts") as string[];

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
            {t("heroBadge")}
          </Badge>
          <h1 className="text-balance text-4xl font-semibold tracking-tight sm:text-6xl">
            {t("heroTitleLine1")}
            <br />
            {t("heroTitleLine2")}
          </h1>
          <p className="text-balance text-lg text-muted-foreground sm:text-xl">
            {t("heroSubtitle")}
          </p>
          <div className="mt-2 flex flex-col items-center gap-3 sm:flex-row">
            <SignInButton forceRedirectUrl="/app">
              <Button size="lg" className="h-11 px-6 text-base">
                {t("ctaPrimary")}
              </Button>
            </SignInButton>
            <Button
              size="lg"
              variant="ghost"
              className="h-11 px-6 text-base"
              render={<a href="#como-funciona">{t("ctaSecondary")}</a>}
            />
          </div>
        </motion.div>
      </section>

      {/* Why not just a chat */}
      <section className="border-t border-border/60 px-6 py-20 sm:py-28">
        <div className="mx-auto max-w-5xl">
          <motion.div {...fadeUp} className="mx-auto mb-12 max-w-2xl text-center">
            <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">{t("whyTitle")}</h2>
            <p className="mt-3 text-muted-foreground">{t("whyBody")}</p>
          </motion.div>
          <div className="grid gap-4 sm:grid-cols-2">
            {differentiators.map((item, i) => {
              const Icon = DIFFERENTIATOR_ICONS[i];
              return (
                <motion.div
                  key={item.title}
                  {...fadeUp}
                  transition={{ ...fadeUp.transition, delay: i * 0.06 }}
                >
                  <Card className="h-full">
                    <CardHeader>
                      <div className="mb-1 flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                        <Icon className="size-4.5" />
                      </div>
                      <CardTitle className="text-base">{item.title}</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground">
                      {item.body}
                    </CardContent>
                  </Card>
                </motion.div>
              );
            })}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="como-funciona" className="border-t border-border/60 px-6 py-20 sm:py-28">
        <div className="mx-auto max-w-5xl">
          <motion.div {...fadeUp} className="mx-auto mb-14 max-w-2xl text-center">
            <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">{t("howTitle")}</h2>
            <p className="mt-3 text-muted-foreground">{t("howBody")}</p>
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
              {t("dualTitle")}
            </h2>
          </motion.div>
          <div className="grid gap-4 sm:grid-cols-2">
            <motion.div {...fadeUp}>
              <Card className="h-full">
                <CardHeader>
                  <Badge variant="secondary" className="mb-2 w-fit">
                    {t("dualOperatorBadge")}
                  </Badge>
                  <CardTitle>{t("dualOperatorTitle")}</CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">
                  {t("dualOperatorBody")}
                </CardContent>
              </Card>
            </motion.div>
            <motion.div {...fadeUp} transition={{ ...fadeUp.transition, delay: 0.08 }}>
              <Card className="h-full">
                <CardHeader>
                  <Badge variant="secondary" className="mb-2 w-fit">
                    {t("dualExecBadge")}
                  </Badge>
                  <CardTitle>{t("dualExecTitle")}</CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">
                  {t("dualExecBody")}
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
              {t("techTitle")}
            </h2>
          </motion.div>
          <motion.ul
            {...fadeUp}
            className="grid gap-x-8 gap-y-4 text-sm text-muted-foreground sm:grid-cols-2"
          >
            {techFacts.map((label, i) => {
              const Icon = TECH_FACT_ICONS[i];
              return (
                <li key={label} className="flex items-start gap-3">
                  <Icon className="mt-0.5 size-4 shrink-0 text-primary" />
                  <span>{label}</span>
                </li>
              );
            })}
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
            {t("finalCtaTitle")}
          </h2>
          <SignInButton forceRedirectUrl="/app">
            <Button size="lg" className="h-11 px-8 text-base">
              {t("ctaPrimary")}
            </Button>
          </SignInButton>
        </motion.div>
      </section>
    </main>
  );
}
