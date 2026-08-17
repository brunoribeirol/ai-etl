import { AnalysisSection } from "@/components/analysis-section";
import { DataTable } from "@/components/data-table";
import { apiFetch } from "@/lib/api";
import type { FullResult } from "@/lib/types";

/**
 * Run-detail page (Sprint 6, PR 5, ADR-011) — replaces `app.py::_render_results`.
 * Server Component: fetches `GET /runs/{run_id}`, which enforces the same
 * server-side tenant-ownership check `load_full_result` already does (404 on
 * mismatch, not just filtered client-side).
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
      <main className="p-8">
        <p className="text-red-600 text-sm">{error ?? "Run não encontrado."}</p>
      </main>
    );
  }

  const silverRows = result.state.transformed_data;

  return (
    <main className="p-8 flex flex-col gap-8 max-w-3xl mx-auto w-full">
      <div>
        <h1 className="text-xl font-semibold font-mono">{runId}</h1>
        <p className="text-sm text-gray-500">{result.state.status}</p>
        {result.question && (
          <p className="text-sm mt-2">O que fazer sobre: &quot;{result.question}&quot;</p>
        )}
      </div>

      {silverRows && (
        <section className="flex flex-col gap-2">
          <h2 className="font-medium">Silver</h2>
          <DataTable rows={silverRows} />
        </section>
      )}

      {result.gold.map((entry, i) => (
        <AnalysisSection key={`gold-${i}`} title="Gold" entry={entry} dataKey="gold_df" />
      ))}

      {result.science.map((entry, i) => (
        <AnalysisSection
          key={`science-${i}`}
          title="Science"
          entry={entry}
          dataKey="predictions_df"
        />
      ))}

      {(result.advisor?.summary || result.advisor?.recommendations?.length > 0) && (
        <section className="flex flex-col gap-2">
          <h2 className="font-medium">Advisor</h2>
          {result.advisor.summary && <p className="text-sm">{result.advisor.summary}</p>}
          <ul className="flex flex-col gap-2">
            {result.advisor.recommendations.map((rec, i) => (
              <li key={i} className="border rounded p-3 text-sm">
                <p className="font-medium">
                  {rec.action}{" "}
                  <span className="text-xs text-gray-400 font-normal">
                    ({rec.priority})
                  </span>
                </p>
                <p className="text-gray-500">{rec.rationale}</p>
              </li>
            ))}
          </ul>
          {result.advisor.error && (
            <p className="text-red-600 text-sm">{result.advisor.error}</p>
          )}
        </section>
      )}
    </main>
  );
}
