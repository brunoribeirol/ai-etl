import { getTranslations } from "next-intl/server";
import { PipelinesManager } from "@/components/pipelines-manager";

/**
 * "Pipelines" page (Sprint 13, ADR-016) — create/pause/resume/edit a saved
 * (recurring) pipeline. Distinct from "/" (avulso `POST /runs`, unchanged).
 *
 * Sprint 25 (ADR-036) — copy comes from `messages/{locale}.json`'s
 * `pipelinesPage` namespace.
 */
export default async function PipelinesPage() {
  const t = await getTranslations("pipelinesPage");
  return (
    <main className="flex-1 px-6 py-12 max-w-4xl mx-auto w-full flex flex-col gap-6 items-center">
      <div className="w-full max-w-2xl">
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t("subtitle")}</p>
      </div>
      <PipelinesManager />
    </main>
  );
}
