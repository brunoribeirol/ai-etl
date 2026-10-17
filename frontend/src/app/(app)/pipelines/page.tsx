import { getTranslations } from "next-intl/server";
import { PipelinesManager } from "@/components/pipelines-manager";

/**
 * "Pipelines" page (Sprint 13, ADR-016): create/pause/resume/edit a saved
 * (recurring) pipeline. Distinct from "/" (avulso `POST /runs`, unchanged).
 *
 * Sprint 25 (ADR-036): copy comes from `messages/{locale}.json`'s
 * `pipelinesPage` namespace.
 */
export default async function PipelinesPage() {
  const t = await getTranslations("pipelinesPage");
  return (
    <main className="flex-1 px-4 sm:px-6 py-10 sm:py-14 max-w-5xl mx-auto w-full flex flex-col gap-6 items-center">
      <div className="w-full max-w-2xl">
        <h1 className="font-serif text-4xl leading-tight tracking-tight text-balance sm:text-5xl">{t("title")}</h1>
        <p className="mt-3 max-w-2xl text-muted-foreground">{t("subtitle")}</p>
      </div>
      <PipelinesManager />
    </main>
  );
}
