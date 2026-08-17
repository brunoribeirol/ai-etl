import { DataTable } from "@/components/data-table";
import { PlotlyChart } from "@/components/plotly-chart";
import type { AnalysisEntry } from "@/lib/types";

/** One Gold or Science sub-task entry — narrative, chart (if any), data
 * preview table, matching `app.py::_render_results`'s per-sub-task layout. */
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

  return (
    <section className="flex flex-col gap-3">
      <h2 className="font-medium">
        {title}
        {entry.task_question ? ` — ${entry.task_question}` : ""}
        {entry.repaired && (
          <span className="text-xs text-amber-600 ml-2">(reparado)</span>
        )}
      </h2>
      {entry.narrative && <p className="text-sm">{entry.narrative}</p>}
      {entry.fig && <PlotlyChart figure={entry.fig} />}
      {rows && <DataTable rows={rows} />}
      {entry.error && <p className="text-red-600 text-sm">{entry.error}</p>}
    </section>
  );
}
