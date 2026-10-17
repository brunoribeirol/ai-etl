import { getTranslations } from "next-intl/server";
import { AnalysisSection } from "@/components/analysis-section";
import { CodeTab } from "@/components/code-tab";
import { DataTable } from "@/components/data-table";
import { PipelineTab } from "@/components/pipeline-tab";
import { RunSections } from "@/components/run-sections";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { apiFetch } from "@/lib/api";
import { splitErrorMessage } from "@/lib/error-message";
import type { FullResult } from "@/lib/types";

/**
 * Run-detail page (Sprint 6, PR 5 → Sprint 7 redesign, ADR-011): replaces
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
  const t = await getTranslations("runDetailPage");

  let result: FullResult | null = null;
  let error: string | null = null;
  try {
    result = await apiFetch<FullResult>(`/runs/${runId}?preview_rows=20`);
  } catch (err) {
    error = String(err);
  }

  if (error || !result) {
    return (
      <main className="flex-1 px-4 sm:px-6 py-10 sm:py-14 max-w-5xl mx-auto w-full">
        <Alert variant="destructive">
          <AlertDescription>{error ?? t("notFound")}</AlertDescription>
        </Alert>
      </main>
    );
  }

  const silverRows = result.state.transformed_data;
  const status = result.state.status ?? "unknown";

  const failure = result.state.error ? splitErrorMessage(result.state.error) : null;

  return (
    <main className="flex-1 px-4 sm:px-6 py-10 sm:py-14 max-w-5xl mx-auto w-full flex flex-col gap-8">
      <div className="flex flex-col gap-3 border-b border-border pb-8">
        {/* 2026-10-16 redesign: the business question is the title; the run id, which was the
            title before, stays as a small line for traceability. */}
        <div className="flex items-center gap-3">
          <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
            {t("eyebrow")}
          </span>
          <StatusBadge status={status} />
        </div>
        <h1 className="font-serif text-3xl leading-tight tracking-tight text-balance sm:text-4xl">
          {result.question || t("noQuestionTitle")}
        </h1>
        <p className="font-mono text-xs break-all text-muted-foreground">{runId}</p>
        {failure && (
          <Alert variant="destructive">
            <AlertDescription className="flex flex-col gap-2">
              <span>{failure.summary}</span>
              {failure.details && (
                <details>
                  <summary className="cursor-pointer text-xs">{t("technicalDetails")}</summary>
                  <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs">
                    {failure.details}
                  </pre>
                </details>
              )}
            </AlertDescription>
          </Alert>
        )}
      </div>

      <RunSections
        silver={
          silverRows ? (
            <>
              <h2 className="font-medium mb-3">{t("silverHeading")}</h2>
              <DataTable rows={silverRows} totalRows={result.state.transformed_row_count} />
            </>
          ) : null
        }
        pipeline={
          <PipelineTab
            stageDurations={result.state.stage_durations}
            pipelinePlan={result.state.pipeline_plan}
            qualityReport={result.state.quality_report}
          />
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
                        ({["high", "medium", "low"].includes(rec.priority)
                          ? t(`priority.${rec.priority}`)
                          : rec.priority})
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
        code={
          <CodeTab
            transformationCode={result.state.transformation_code}
            gold={result.gold}
            science={result.science}
          />
        }
      />
    </main>
  );
}
