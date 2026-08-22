"use client";

import { useAuth } from "@clerk/nextjs";
import { ArrowDown, ArrowUp, Minus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import type { FullResult, PipelineRunHistoryEntry, SavedPipeline } from "@/lib/types";

/**
 * Sprint 18 (ADR-024) — the executive-facing counterpart to the technical
 * run-detail page (`historico/[runId]/page.tsx`'s Pipeline/Código tabs).
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

const STATUS_LABEL: Record<string, string> = {
  completed: "Concluído com sucesso",
  success: "Concluído com sucesso",
  failed: "Falhou",
  failure: "Falhou",
  error: "Falhou",
  running: "Em execução",
  started: "Em execução",
  pending: "Aguardando execução",
};

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

function statusLabel(status: string): string {
  return STATUS_LABEL[status.toLowerCase()] ?? status;
}

function statusTone(status: string): string {
  return STATUS_TONE[status.toLowerCase()] ?? "text-muted-foreground";
}

function KpiCard({
  label,
  current,
  previous,
  format,
}: {
  label: string;
  current: number | null;
  previous: number | null;
  format: (v: number) => string;
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
              ? `${Math.abs(pctChange).toFixed(0)}% em relação à última execução`
              : "mudou em relação à última execução"}
          </span>
        )}
        {delta === 0 && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Minus className="h-3 w-3" /> sem mudança
          </span>
        )}
      </CardContent>
    </Card>
  );
}

export function ExecutiveSummary({ pipeline }: { pipeline: SavedPipeline }) {
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
    setLoading(true);
    setError(null);

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
    return <p className="text-sm text-muted-foreground">Carregando resumo...</p>;
  }
  if (error) {
    return (
      <p className="text-destructive text-sm" role="alert">
        {error}
      </p>
    );
  }
  if (!history || history.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Este pipeline ainda não executou — volte aqui depois da primeira execução agendada.
      </p>
    );
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
          Última atualização: {new Date(latest.timestamp).toLocaleString()}
        </span>
      </div>

      {latest.status.toLowerCase() === "failed" && (
        <Card>
          <CardContent>
            <p className="text-sm text-red-400">
              A última execução deste pipeline não terminou com sucesso
              {latest.error ? `: ${latest.error}` : "."} A equipe técnica já foi avisada.
            </p>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <KpiCard
          label="Linhas processadas"
          current={latest.rows_loaded}
          previous={previous?.rows_loaded ?? null}
          format={(v) => v.toLocaleString("pt-BR")}
        />
        <KpiCard
          label="Custo da execução"
          current={latest.cost_usd}
          previous={previous?.cost_usd ?? null}
          format={(v) => `US$ ${v.toFixed(4)}`}
        />
        <KpiCard
          label="Uso de IA (tokens)"
          current={latest.total_tokens}
          previous={previous?.total_tokens ?? null}
          format={(v) => v.toLocaleString("pt-BR")}
        />
      </div>

      {latestRun?.advisor && (latestRun.advisor.summary || latestRun.advisor.recommendations?.length > 0) ? (
        <div className="flex flex-col gap-4">
          {latestRun.advisor.summary && (
            <Card>
              <CardContent>
                <h2 className="text-sm font-medium mb-2">O que aconteceu</h2>
                <p className="text-sm text-muted-foreground">{latestRun.advisor.summary}</p>
              </CardContent>
            </Card>
          )}
          {latestRun.advisor.recommendations?.length > 0 && (
            <Card>
              <CardContent>
                <h2 className="text-sm font-medium mb-3">Recomendações</h2>
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
          <p className="text-sm text-muted-foreground">
            Este pipeline não tem uma pergunta de negócio configurada, então não há um resumo
            de análise para mostrar aqui — apenas os números acima.
          </p>
        )
      )}
    </div>
  );
}
