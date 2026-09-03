"use client";

import { useAuth } from "@clerk/nextjs";
import { ArrowDown, ArrowUp, Minus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { friendlyExecutiveError } from "@/lib/friendly-error";
import type { FullResult, PipelineRunHistoryEntry, SavedPipeline } from "@/lib/types";

/**
 * Sprint 18 (ADR-024) — the executive-facing counterpart to the technical
 * run-detail page (`history/[runId]/page.tsx`'s Pipeline/Code tabs).
 * Deliberately does not render either tab: this component only shows what a
 * non-technical stakeholder needs — plain-language status, the KPIs that
 * changed since the last run, and the Advisor's existing narrative/
 * recommendations (Sprint 14's digest content, reused as-is per ADR-024 —
 * no new agent, no new LLM call here).
 *
 * Composes three already-existing, already-tenant-scoped endpoints
 * (`GET /pipelines/{id}/history`, `GET /runs/{run_id}`) — no new backend
 * surface, see ADR-024. Client Component for the same reason
 * `PipelineHistory`/`PipelinesManager` are: `useAuth().getToken()` needs a
 * fresh token per request.
 */

// Sprint 25 (ADR-036) — labels come from `messages/{locale}.json`'s
// `executiveSummary` namespace (built into a lookup map inside the
// component, since this constant needs `useTranslations`); tone (color) is
// locale-independent and stays a plain module-level map.
function buildStatusLabels(t: (key: string) => string): Record<string, string> {
  return {
    completed: t("statusCompleted"),
    success: t("statusCompleted"),
    failed: t("statusFailed"),
    failure: t("statusFailed"),
    error: t("statusFailed"),
    running: t("statusRunning"),
    started: t("statusRunning"),
    pending: t("statusPending"),
  };
}

const STATUS_TONE: Record<string, string> = {
  completed: "text-emerald-400",
  success: "text-emerald-400",
  failed: "text-red-400",
  failure: "text-red-400",
  error: "text-red-400",
  running: "text-blue-400",
  started: "text-blue-400",
  pending: "text-amber-400",
};

function statusTone(status: string): string {
  return STATUS_TONE[status.toLowerCase()] ?? "text-muted-foreground";
}

function KpiCard({
  label,
  current,
  previous,
  format,
  t,
}: {
  label: string;
  current: number | null;
  previous: number | null;
  format: (v: number) => string;
  t: ReturnType<typeof useTranslations>;
}) {
  const delta = current !== null && previous !== null ? current - previous : null;
  const pctChange =
    delta !== null && previous !== null && previous !== 0 ? (delta / previous) * 100 : null;

  return (
    <Card>
      <CardContent className="flex flex-col gap-1">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="text-2xl font-semibold tabular-nums">
          {current !== null ? format(current) : "—"}
        </span>
        {delta !== null && delta !== 0 && (
          <span
            className={`flex items-center gap-1 text-xs ${
              delta > 0 ? "text-emerald-400" : "text-red-400"
            }`}
          >
            {delta > 0 ? (
              <ArrowUp className="h-3 w-3" />
            ) : (
              <ArrowDown className="h-3 w-3" />
            )}
            {pctChange !== null
              ? t("deltaPercent", { percent: Math.abs(pctChange).toFixed(0) })
              : t("deltaChanged")}
          </span>
        )}
        {delta === 0 && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Minus className="h-3 w-3" /> {t("deltaUnchanged")}
          </span>
        )}
      </CardContent>
    </Card>
  );
}

export function ExecutiveSummary({ pipeline }: { pipeline: SavedPipeline }) {
  const t = useTranslations("executiveSummary");
  const tErrors = useTranslations("executiveErrors");
  const statusLabels = buildStatusLabels(t);
  const statusLabel = (status: string) => statusLabels[status.toLowerCase()] ?? status;
  const { getToken } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;

  const [history, setHistory] = useState<PipelineRunHistoryEntry[] | null>(null);
  const [latestRun, setLatestRun] = useState<FullResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const authedFetch = useCallback(
    async <T,>(path: string): Promise<T> => {
      if (!apiUrl) throw new Error("NEXT_PUBLIC_API_URL is not configured.");
      const token = await getToken();
      const response = await fetch(`${apiUrl}${path}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail ?? `HTTP ${response.status}`);
      }
      return response.json();
    },
    [apiUrl, getToken],
  );

  useEffect(() => {
    let cancelled = false;
    // Intentional synchronous loading/error reset before a cancellable
    // fetch, so a pipeline.id change immediately shows the loading state
    // instead of a stale summary for one frame; same established pattern as
    // pipeline-history.tsx and pipelines-manager.tsx. No data-fetching
    // library (SWR/React Query) in this codebase to delegate this to.
    /* eslint-disable react-hooks/set-state-in-effect */
    setLoading(true);
    setError(null);
    /* eslint-enable react-hooks/set-state-in-effect */

    async function load() {
      try {
        // Small window: only the two most recent fires are needed for the
        // "what changed since last time" comparison (ADR-024's client-side
        // approximation of Sprint 14's drift check).
        const runs = await authedFetch<PipelineRunHistoryEntry[]>(
          `/pipelines/${pipeline.id}/history?limit=2`,
        );
        if (cancelled) return;
        setHistory(runs);

        const latest = runs[runs.length - 1];
        if (latest && latest.status.toLowerCase() === "completed") {
          const full = await authedFetch<FullResult>(`/runs/${latest.run_id}`);
          if (!cancelled) setLatestRun(full);
        }
      } catch (err) {
        if (!cancelled) setError(String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [authedFetch, pipeline.id]);

  if (loading) {
    return <p className="text-sm text-muted-foreground">{t("loading")}</p>;
  }
  if (error) {
    return (
      <p className="text-destructive text-sm" role="alert">
        {friendlyExecutiveError(error, tErrors)}
      </p>
    );
  }
  if (!history || history.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("emptyHistory")}</p>;
  }

  const latest = history[history.length - 1];
  const previous = history.length > 1 ? history[history.length - 2] : null;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex items-center gap-3">
        <span className={`text-sm font-medium ${statusTone(latest.status)}`}>
          {statusLabel(latest.status)}
        </span>
        <span className="text-xs text-muted-foreground">
          {t("lastUpdated", { when: new Date(latest.timestamp).toLocaleString() })}
        </span>
      </div>

      {latest.status.toLowerCase() === "failed" && (
        <Card>
          <CardContent>
            {/* Sprint 38 — previously interpolated `latest.error` (a raw
                backend exception message, e.g. stack-trace-adjacent Python
                text) straight into this executive-facing sentence. That
                detail belongs on the technical "History" page, not here —
                this card now just says what happened and what to expect,
                in plain language. */}
            <p className="text-sm text-red-400">{t("failedNotice")}</p>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <KpiCard
          label={t("kpiRowsLoaded")}
          current={latest.rows_loaded}
          previous={previous?.rows_loaded ?? null}
          format={(v) => v.toLocaleString("pt-BR")}
          t={t}
        />
        <KpiCard
          label={t("kpiCost")}
          current={latest.cost_usd}
          previous={previous?.cost_usd ?? null}
          format={(v) => `US$ ${v.toFixed(4)}`}
          t={t}
        />
        <KpiCard
          label={t("kpiTokens")}
          current={latest.total_tokens}
          previous={previous?.total_tokens ?? null}
          format={(v) => v.toLocaleString("pt-BR")}
          t={t}
        />
      </div>

      {latestRun?.advisor && (latestRun.advisor.summary || latestRun.advisor.recommendations?.length > 0) ? (
        <div className="flex flex-col gap-4">
          {latestRun.advisor.summary && (
            <Card>
              <CardContent>
                <h2 className="text-sm font-medium mb-2">{t("whatHappenedHeading")}</h2>
                <p className="text-sm text-muted-foreground">{latestRun.advisor.summary}</p>
              </CardContent>
            </Card>
          )}
          {latestRun.advisor.recommendations?.length > 0 && (
            <Card>
              <CardContent>
                <h2 className="text-sm font-medium mb-3">{t("recommendationsHeading")}</h2>
                <ul className="flex flex-col gap-3">
                  {latestRun.advisor.recommendations.map((rec, i) => (
                    <li key={i} className="border border-border/60 rounded-lg p-3 text-sm">
                      <p className="font-medium">{rec.action}</p>
                      <p className="text-muted-foreground mt-1">{rec.rationale}</p>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>
      ) : (
        latest.status.toLowerCase() === "completed" && (
          <p className="text-sm text-muted-foreground">{t("noSummary")}</p>
        )
      )}
    </div>
  );
}
