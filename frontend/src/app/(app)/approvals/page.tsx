import { getTranslations } from "next-intl/server";
import { ApprovalQueue } from "@/components/approval-queue";

/**
 * "Approvals" page (Wave 6, 2026-08-25 admin panel/approval-gate UI plan):
 * first frontend consumer of the Sprint 27 (ADR-028) approval-gate routes.
 * Visible to every authenticated user, same as every other page in this
 * route group: this app makes no viewer/editor UI distinction anywhere
 * (see `pipelines-manager.tsx`); `approve`/`reject` are `editor`-gated
 * server-side regardless of what this page shows.
 */
export default async function ApprovalsPage() {
  const t = await getTranslations("approvalsPage");
  return (
    <main className="flex-1 px-4 sm:px-6 py-10 sm:py-14 max-w-5xl mx-auto w-full flex flex-col gap-6">
      <div>
        <h1 className="font-serif text-4xl leading-tight tracking-tight text-balance sm:text-5xl">{t("title")}</h1>
        <p className="mt-3 max-w-2xl text-muted-foreground">{t("subtitle")}</p>
      </div>
      <ApprovalQueue />
    </main>
  );
}
