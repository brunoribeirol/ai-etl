import { getTranslations } from "next-intl/server";
import { ExecutarForm } from "@/components/executar-form";

/**
 * "Executar" page (Sprint 6, PR 4 → Sprint 7 redesign, ADR-011). Same route
 * and behavior — the API call and auth flow live entirely in
 * `<ExecutarForm />`, unchanged; this is layout/copy only.
 *
 * Sprint 25 (ADR-036) — copy comes from `messages/{locale}.json`'s
 * `executarPage` namespace.
 */
export default async function Home() {
  const t = await getTranslations("executarPage");
  return (
    <main className="flex-1 flex flex-col items-center px-6 py-16 gap-10">
      <div className="flex flex-col items-center gap-2 text-center max-w-lg">
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>
      <ExecutarForm />
    </main>
  );
}
