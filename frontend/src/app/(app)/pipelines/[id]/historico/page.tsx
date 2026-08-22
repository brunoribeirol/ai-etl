import Link from "next/link";
import { PipelineHistory } from "@/components/pipeline-history";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { apiFetch } from "@/lib/api";
import type { SavedPipeline } from "@/lib/types";

/**
 * Sprint 17 (ADR-017) — "Histórico comparável" for one saved pipeline: a
 * time-series view of its KPIs across every execution, plus a diff between
 * two runs the user picks. Distinct from "/historico" (every avulso + every
 * scheduled run, flat, no grouping) and from "/pipelines" (CRUD, no run
 * history at all) — this page is the missing link between the two.
 *
 * Server-fetches the pipeline itself (name/spec for the header, and to 404
 * early if it isn't this tenant's) the same way `historico/[runId]/page.tsx`
 * does; the actual history + diff UI is a Client Component (`PipelineHistory`)
 * since it needs `useAuth().getToken()` fresh per request, same as
 * `PipelinesManager`.
 */
export default async function PipelineHistoryPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let pipeline: SavedPipeline | null = null;
  let error: string | null = null;
  try {
    pipeline = await apiFetch<SavedPipeline>(`/pipelines/${id}`);
  } catch (err) {
    error = String(err);
  }

  if (error || !pipeline) {
    return (
      <main className="flex-1 px-6 py-12 max-w-4xl mx-auto w-full">
        <Alert variant="destructive">
          <AlertDescription>{error ?? "Pipeline não encontrado."}</AlertDescription>
        </Alert>
      </main>
    );
  }

  return (
    <main className="flex-1 px-6 py-12 max-w-4xl mx-auto w-full flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <Link
          href="/pipelines"
          className="text-xs text-muted-foreground hover:text-foreground w-fit"
        >
          ← Pipelines agendados
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">{pipeline.name}</h1>
        <p className="text-sm text-muted-foreground font-mono">
          {pipeline.source_type} · {pipeline.cron_schedule}
        </p>
      </div>

      <PipelineHistory pipelineId={pipeline.id} />
    </main>
  );
}
