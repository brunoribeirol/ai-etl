import { getTranslations } from "next-intl/server";
import { ApprovalQueue } from "@/components/approval-queue";

/**
 * "Aprovações" page (Wave 6, 2026-08-25 admin panel/approval-gate UI plan) —
 * first frontend consumer of the Sprint 27 (ADR-028) approval-gate routes.
 * Visible to every authenticated user, same as every other page in this
 * route group — this app makes no viewer/editor UI distinction anywhere
 * (see `pipelines-manager.tsx`); `approve`/`reject` are `editor`-gated
 * server-side regardless of what this page shows.
 */
export default async function AprovacoesPage() {
  const t = await getTranslations("aprovacoesPage");
  return (
    <main className="flex-1 px-6 py-12 max-w-4xl mx-auto w-full flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t("subtitle")}</p>
      </div>
      <ApprovalQueue />
    </main>
  );
}
