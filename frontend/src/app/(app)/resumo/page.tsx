import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { friendlyExecutiveError } from "@/lib/friendly-error";
import type { SavedPipeline } from "@/lib/types";

/**
 * Sprint 18 (ADR-024) — "Resumo" is the executive entry point, separate
 * from "/pipelines" (technical CRUD) and "/historico" (technical run list,
 * keyed by `run_id`). Navigation here is by business question, not by
 * pipeline internals — each card leads with `business_question`, not
 * `source_type`/`cron_schedule`/`spec` the way `pipelines-manager.tsx` does.
 *
 * Scoped to saved (recurring) pipelines only — see ADR-024 for why an
 * avulso run has no business-question-first identity to navigate by.
 */
export default async function ResumoIndexPage() {
  let pipelines: SavedPipeline[] = [];
  let error: string | null = null;
  try {
    pipelines = await apiFetch<SavedPipeline[]>("/pipelines");
  } catch (err) {
    error = String(err);
  }

  return (
    <main className="flex-1 px-6 py-12 max-w-3xl mx-auto w-full flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Resumo executivo</h1>
        <p className="text-sm text-muted-foreground mt-1">
          O resultado dos seus pipelines, em linguagem simples — sem código, sem termos
          técnicos.
        </p>
      </div>

      {error && (
        <p className="text-destructive text-sm" role="alert">
          {friendlyExecutiveError(error)}
        </p>
      )}

      {!error && pipelines.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Nenhum pipeline agendado ainda. Crie um em &ldquo;Pipelines&rdquo; para ver o resumo
          aqui depois da primeira execução.
        </p>
      )}

      <div className="flex flex-col gap-3">
        {pipelines.map((pipeline) => (
          <Link key={pipeline.id} href={`/resumo/${pipeline.id}`}>
            <Card className="hover:border-foreground/30 transition-colors">
              <CardContent className="flex flex-col gap-1">
                <span className="font-medium">
                  {pipeline.business_question || pipeline.name}
                </span>
                {pipeline.business_question && (
                  <span className="text-xs text-muted-foreground">{pipeline.name}</span>
                )}
                {pipeline.consecutive_failures > 0 && (
                  <span className="text-xs text-red-400 mt-1">Precisa de atenção</span>
                )}
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </main>
  );
}
