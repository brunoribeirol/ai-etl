"use client";

import { useAuth } from "@clerk/nextjs";
import { CheckCircle2, Circle } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { OnboardingStatus } from "@/lib/types";

/**
 * Sprint 26 (ADR-027) — activation checklist for `/comecar`. Fetches
 * `GET /onboarding/status`, a read-only aggregate over `runs`/
 * `saved_pipelines` (no dedicated table, see the ADR). Deliberately scoped
 * to this one page rather than repeated in the header on every route — a
 * known, flagged scope cut, same posture Sprint 13's manager took.
 */
export function OnboardingChecklist() {
  const t = useTranslations("onboardingChecklist");
  const { getToken } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!apiUrl) {
        setError(t("missingApiUrl"));
        return;
      }
      try {
        const token = await getToken();
        const response = await fetch(`${apiUrl}/onboarding/status`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data: OnboardingStatus = await response.json();
        if (!cancelled) setStatus(data);
      } catch (err) {
        if (!cancelled) setError(String(err));
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [apiUrl, getToken, t]);

  const items = [
    {
      done: status?.has_completed_run ?? false,
      label: t("item1Label"),
      hint: t("item1Hint"),
      href: null,
    },
    {
      done: status?.has_saved_pipeline ?? false,
      label: t("item2Label"),
      hint: t("item2Hint"),
      href: "/pipelines",
    },
    {
      done: status?.has_saved_pipeline ?? false,
      label: t("item3Label"),
      hint: t("item3Hint"),
      href: "/resumo",
    },
  ];

  return (
    <Card>
      <CardContent className="flex flex-col gap-3">
        <span className="text-sm font-medium">O que falta para estar pronto</span>
        {error && (
          <p className="text-destructive text-xs" role="alert">
            {error}
          </p>
        )}
        <ul className="flex flex-col gap-3">
          {items.map((item) => (
            <li key={item.label} className="flex gap-3 items-start">
              {item.done ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
              ) : (
                <Circle className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
              )}
              <div className="flex flex-col gap-0.5">
                <span className={cn("text-sm", item.done && "text-muted-foreground line-through")}>
                  {item.href && !item.done ? (
                    <Link href={item.href} className="hover:underline">
                      {item.label}
                    </Link>
                  ) : (
                    item.label
                  )}
                </span>
                <span className="text-xs text-muted-foreground">{item.hint}</span>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
