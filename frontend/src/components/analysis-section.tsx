import { DataTable } from "@/components/data-table";
import { PlotlyChart } from "@/components/plotly-chart";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AnalysisEntry } from "@/lib/types";

/** One Gold or Science sub-task entry — narrative, chart (if any), data
 * preview table, matching `app.py::_render_results`'s per-sub-task layout.
 * Sprint 7: wrapped in a Card instead of a bare `<section>`, same content. */
export function AnalysisSection({
  title,
  entry,
  dataKey,
}: {
  title: string;
  entry: AnalysisEntry;
  dataKey: "gold_df" | "predictions_df";
}) {
  const rows = entry[dataKey];
  const sanityCheck = entry.sanity_check;
  const hasCaveat = sanityCheck && sanityCheck.severity !== "ok";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {title}
          {entry.task_question ? ` — ${entry.task_question}` : ""}
          {entry.repaired && (
            <Badge variant="outline" className="text-amber-400 border-amber-500/30 bg-amber-500/10">
              reparado
            </Badge>
          )}
          {hasCaveat && (
            <Badge
              variant="outline"
              className="text-amber-400 border-amber-500/30 bg-amber-500/10"
              title={sanityCheck.summary}
            >
              ressalva
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {entry.narrative && <p className="text-sm text-muted-foreground">{entry.narrative}</p>}
        {hasCaveat && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-sm text-amber-400">
            <p className="font-medium">Resultado com ressalva de sanity-check</p>
            <ul className="mt-1 list-disc pl-4">
              {sanityCheck.checks
                .filter((c) => c.severity !== "ok")
                .map((c, i) => (
                  <li key={`${c.check}-${i}`}>{c.detail}</li>
                ))}
            </ul>
          </div>
        )}
        {entry.fig && <PlotlyChart figure={entry.fig} />}
        {rows && <DataTable rows={rows} />}
        {entry.error && <p className="text-destructive text-sm">{entry.error}</p>}
      </CardContent>
    </Card>
  );
}
