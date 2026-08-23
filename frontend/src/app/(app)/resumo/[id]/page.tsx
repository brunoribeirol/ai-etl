import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { ExecutiveSummary } from "@/components/executive-summary";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { apiFetch } from "@/lib/api";
import { friendlyExecutiveError } from "@/lib/friendly-error";
import type { SavedPipeline } from "@/lib/types";

/**
 * Sprint 18 (ADR-024) — executive summary for one saved pipeline: plain-
 * language status, the KPIs that changed since the last run, and the
 * Advisor's existing narrative/recommendations (Sprint 14's digest content).
 * Deliberately not a variant of `historico/[runId]/page.tsx` — no Pipeline
 * tab, no Código tab, no `run_id` in the page's own identity (the header is
 * the business question, not a run id).
 *
 * Server-fetches the pipeline itself (business question for the header, and
 * to 404 early if it isn't this tenant's), same pattern as
 * `pipelines/[id]/historico/page.tsx`; the actual summary is a Client
 * Component (`ExecutiveSummary`) since it needs `useAuth().getToken()`.
 */
export default async function ResumoPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const t = await getTranslations("resumoPage");

  let pipeline: SavedPipeline | null = null;
  let error: string | null = null;
  try {
    pipeline = await apiFetch<SavedPipeline>(`/pipelines/${id}`);
  } catch (err) {
    error = String(err);
  }

  if (error || !pipeline) {
    return (
      <main className="flex-1 px-6 py-12 max-w-3xl mx-auto w-full">
        <Alert variant="destructive">
          <AlertDescription>
            {/* Sprint 38 — friendlyExecutiveError still returns hardcoded
                Portuguese text regardless of locale; out of this task's file
                list (lib/friendly-error.ts), left untouched. */}
            {error ? friendlyExecutiveError(error) : t("notFound")}
          </AlertDescription>
        </Alert>
      </main>
    );
  }

  return (
    <main className="flex-1 px-6 py-12 max-w-3xl mx-auto w-full flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <Link href="/resumo" className="text-xs text-muted-foreground hover:text-foreground w-fit">
          {t("backLink")}
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">
          {pipeline.business_question || pipeline.name}
        </h1>
        {pipeline.business_question && (
          <p className="text-sm text-muted-foreground">{pipeline.name}</p>
        )}
      </div>

      <ExecutiveSummary pipeline={pipeline} />
    </main>
  );
}
