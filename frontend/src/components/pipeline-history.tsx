"use client";

import { useAuth } from "@clerk/nextjs";
import { ArrowRight } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Figure } from "react-plotly.js";
import { PlotlyChart } from "@/components/plotly-chart";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { AnalysisEntry, FullResult, PipelineRunHistoryEntry } from "@/lib/types";

/**
 * Sprint 17 (ADR-017) — time-series KPI view + two-run diff for one saved
 * pipeline. Client Component: needs `useAuth().getToken()` fresh per
 * request, same pattern `PipelinesManager` already established.
 *
 * Reuses `<PlotlyChart>` (ADR-011/ADR-009's `{data, layout}` shape) for the
 * time series instead of a new charting component — the figure is built
 * client-side from `GET /pipelines/{id}/history`'s plain KPI rows, not a
 * backend-serialized `fig.to_plotly_json()`, since this view has no single
 * agent-generated figure to reuse (it's the aggregate across runs).
 *
 * The diff panel fetches the two selected runs' full results via the
 * existing `GET /runs/{run_id}` (no new backend diff endpoint — ADR-017's
 * own scope note) and computes the diff entirely client-side.
 */
export function PipelineHistory({ pipelineId }: { pipelineId: string }) {
  const { getToken } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;

  const [history, setHistory] = useState<PipelineRunHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [runA, setRunA] = useState<string | null>(null);
  const [runB, setRunB] = useState<string | null>(null);
  const [diffA, setDiffA] = useState<FullResult | null>(null);
  const [diffB, setDiffB] = useState<FullResult | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);

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
    authedFetch<PipelineRunHistoryEntry[]>(`/pipelines/${pipelineId}/history`)
      .then((data) => {
        if (!cancelled) setHistory(data);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [authedFetch, pipelineId]);

  const chartFigure: Partial<Figure> = useMemo(() => {
    const x = history.map((h) => h.timestamp);
    return {
      data: [
        {
          x,
          y: history.map((h) => h.rows_loaded),
          name: "Linhas carregadas",
          type: "scatter",
          mode: "lines+markers",
          yaxis: "y",
        },
        {
          x,
          y: history.map((h) => h.total_tokens),
          name: "Tokens totais",
          type: "scatter",
          mode: "lines+markers",
          yaxis: "y2",
        },
        {
          x,
          y: history.map((h) => h.cost_usd),
          name: "Custo (USD)",
          type: "scatter",
          mode: "lines+markers",
          yaxis: "y3",
        },
      ],
      layout: {
        legend: { orientation: "h", y: -0.2 },
        xaxis: { title: { text: "Execução" } },
        yaxis: { title: { text: "Linhas" }, domain: [0, 1] },
        yaxis2: { overlaying: "y", side: "right", title: { text: "Tokens" }, showgrid: false },
        yaxis3: { visible: false, overlaying: "y" },
      },
    };
  }, [history]);

  async function loadDiff() {
    if (!runA || !runB) return;
    setDiffLoading(true);
    setDiffError(null);
    try {
      const [a, b] = await Promise.all([
        authedFetch<FullResult>(`/runs/${runA}`),
        authedFetch<FullResult>(`/runs/${runB}`),
      ]);
      setDiffA(a);
      setDiffB(b);
    } catch (err) {
      setDiffError(String(err));
    } finally {
      setDiffLoading(false);
    }
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Carregando histórico...</p>;
  }
  if (error) {
    return (
      <p className="text-destructive text-sm" role="alert">
        {error}
      </p>
    );
  }
  if (history.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Este pipeline ainda não executou nenhuma vez.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <Card>
        <CardContent>
          <h2 className="text-sm font-medium mb-2">KPIs ao longo do tempo</h2>
          <PlotlyChart figure={chartFigure} />
        </CardContent>
      </Card>

      <div className="flex flex-col gap-3">
        <h2 className="text-sm font-medium text-muted-foreground">
          Execuções ({history.length})
        </h2>
        <div className="flex flex-col gap-2">
          {history.map((run) => (
            <div
              key={run.run_id}
              className="flex items-center gap-3 text-sm border border-border/60 rounded-lg p-3"
            >
              <StatusBadge status={run.status} />
              <span className="font-mono text-xs truncate flex-1">{run.run_id}</span>
              <span className="text-xs text-muted-foreground">
                {new Date(run.timestamp).toLocaleString()}
              </span>
              <span className="text-xs text-muted-foreground w-20 text-right">
                {run.rows_loaded ?? "—"} linhas
              </span>
              <span className="text-xs text-muted-foreground w-24 text-right">
                {run.cost_usd != null ? `US$ ${run.cost_usd.toFixed(4)}` : "—"}
              </span>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant={runA === run.run_id ? "default" : "outline"}
                  onClick={() => setRunA(run.run_id)}
                >
                  A
                </Button>
                <Button
                  size="sm"
                  variant={runB === run.run_id ? "default" : "outline"}
                  onClick={() => setRunB(run.run_id)}
                >
                  B
                </Button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-medium text-muted-foreground">
            Diff entre execuções
          </h2>
          <Button size="sm" disabled={!runA || !runB || diffLoading} onClick={loadDiff}>
            {diffLoading ? "Comparando..." : "Comparar A → B"}
          </Button>
        </div>
        {diffError && (
          <p className="text-destructive text-sm" role="alert">
            {diffError}
          </p>
        )}
        {diffA && diffB && <RunDiff a={diffA} b={diffB} />}
      </div>
    </div>
  );
}

function _num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** One numeric metric row with a colored delta — green improvement is
 * ambiguous per-metric (more rows isn't always "better"), so this only
 * signals direction (▲/▼), not good/bad. */
function DeltaRow({
  label,
  a,
  b,
  format = (v: number) => String(v),
}: {
  label: string;
  a: number | null;
  b: number | null;
  format?: (v: number) => string;
}) {
  if (a === null && b === null) return null;
  const delta = a !== null && b !== null ? b - a : null;
  return (
    <div className="flex items-center justify-between text-sm py-1.5 border-b border-border/40 last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <div className="flex items-center gap-2 font-mono">
        <span>{a !== null ? format(a) : "—"}</span>
        <ArrowRight className="h-3 w-3 text-muted-foreground" />
        <span>{b !== null ? format(b) : "—"}</span>
        {delta !== null && delta !== 0 && (
          <span className={delta > 0 ? "text-emerald-400" : "text-red-400"}>
            ({delta > 0 ? "+" : ""}
            {format(delta)})
          </span>
        )}
      </div>
    </div>
  );
}

function RunDiff({ a, b }: { a: FullResult; b: FullResult }) {
  const goldByQuestion = useMemo(() => matchByQuestion(a.gold, b.gold), [a.gold, b.gold]);
  const scienceByQuestion = useMemo(
    () => matchByQuestion(a.science, b.science),
    [a.science, b.science],
  );

  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <div>
          <h3 className="text-sm font-medium mb-1">Silver</h3>
          <DeltaRow
            label="Linhas carregadas"
            a={_num(a.state.transformed_data?.length)}
            b={_num(b.state.transformed_data?.length)}
          />
        </div>

        {goldByQuestion.length > 0 && (
          <div>
            <h3 className="text-sm font-medium mb-1">Gold</h3>
            {goldByQuestion.map(([question, entryA, entryB]) => (
              <div key={question} className="mb-2">
                <p className="text-xs text-muted-foreground truncate">{question}</p>
                <DeltaRow
                  label="Linhas no resultado"
                  a={_num(entryA?.data_shape?.[0])}
                  b={_num(entryB?.data_shape?.[0])}
                />
              </div>
            ))}
          </div>
        )}

        {scienceByQuestion.length > 0 && (
          <div>
            <h3 className="text-sm font-medium mb-1">Science</h3>
            {scienceByQuestion.map(([question, entryA, entryB]) => (
              <div key={question} className="mb-2">
                <p className="text-xs text-muted-foreground truncate">{question}</p>
                {modelInfoKeys(entryA, entryB).map((key) => (
                  <DeltaRow
                    key={key}
                    label={key}
                    a={_num(entryA?.model_info?.[key])}
                    b={_num(entryB?.model_info?.[key])}
                    format={(v) => v.toFixed(4)}
                  />
                ))}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function matchByQuestion(
  listA: AnalysisEntry[],
  listB: AnalysisEntry[],
): [string, AnalysisEntry | undefined, AnalysisEntry | undefined][] {
  const questions = new Set<string>();
  for (const e of listA) if (e.task_question) questions.add(e.task_question);
  for (const e of listB) if (e.task_question) questions.add(e.task_question);
  return Array.from(questions).map((q) => [
    q,
    listA.find((e) => e.task_question === q),
    listB.find((e) => e.task_question === q),
  ]);
}

function modelInfoKeys(a: AnalysisEntry | undefined, b: AnalysisEntry | undefined): string[] {
  const keys = new Set<string>();
  for (const [k, v] of Object.entries(a?.model_info ?? {})) if (typeof v === "number") keys.add(k);
  for (const [k, v] of Object.entries(b?.model_info ?? {})) if (typeof v === "number") keys.add(k);
  return Array.from(keys);
}
