import { AnalysisSection } from "@/components/analysis-section";
import { DataTable } from "@/components/data-table";
import { RunSections } from "@/components/run-sections";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { apiFetch } from "@/lib/api";
import type { FullResult } from "@/lib/types";

/**
 * Run-detail page (Sprint 6, PR 5 → Sprint 7 redesign, ADR-011) — replaces
 * `app.py::_render_results`. Same server-side `GET /runs/{run_id}` fetch
 * (tenant-ownership check enforced server-side, 404 on mismatch); the
 * previously flat vertical stack is now grouped into tabs
 * (`<RunSections>`, a Client Component for the staggered entrance
 * animation) so a run with several Gold/Science sub-tasks doesn't turn into
 * one long scroll.
 */
export default async function RunDetail({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;

  let result: FullResult | null = null;
  let error: string | null = null;
  try {
    result = await apiFetch<FullResult>(`/runs/${runId}`);
  } catch (err) {
    error = String(err);
  }

  if (error || !result) {
    return (
      <main className="flex-1 px-6 py-12 max-w-4xl mx-auto w-full">
        <Alert variant="destructive">
          <AlertDescription>{error ?? "Run não encontrado."}</AlertDescription>
        </Alert>
      </main>
    );
  }

  const silverRows = result.state.transformed_data;
  const status = result.state.status ?? "unknown";

  return (
    <main className="flex-1 px-6 py-12 max-w-4xl mx-auto w-full flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold font-mono truncate">{runId}</h1>
          <StatusBadge status={status} />
        </div>
        {result.question && (
          <p className="text-sm text-muted-foreground">
            O que fazer sobre: &quot;{result.question}&quot;
          </p>
        )}
      </div>

      <RunSections
        silver={
          silverRows ? (
            <>
              <h2 className="font-medium mb-3">Silver</h2>
              <DataTable rows={silverRows} />
            </>
          ) : null
        }
        gold={result.gold.map((entry, i) => (
          <AnalysisSection key={`gold-${i}`} title="Gold" entry={entry} dataKey="gold_df" />
        ))}
        science={result.science.map((entry, i) => (
          <AnalysisSection
            key={`science-${i}`}
            title="Science"
            entry={entry}
            dataKey="predictions_df"
          />
        ))}
        advisor={
          result.advisor?.summary || result.advisor?.recommendations?.length > 0 ? (
            <div className="flex flex-col gap-3">
              {result.advisor.summary && (
                <p className="text-sm text-muted-foreground">{result.advisor.summary}</p>
              )}
              <ul className="flex flex-col gap-2">
                {result.advisor.recommendations.map((rec, i) => (
                  <li key={i} className="border border-border/60 rounded-lg p-3 text-sm">
                    <p className="font-medium">
                      {rec.action}{" "}
                      <span className="text-xs text-muted-foreground font-normal">
                        ({rec.priority})
                      </span>
                    </p>
                    <p className="text-muted-foreground">{rec.rationale}</p>
                  </li>
                ))}
              </ul>
              {result.advisor.error && (
                <p className="text-destructive text-sm">{result.advisor.error}</p>
              )}
            </div>
          ) : null
        }
      />
    </main>
  );
}
