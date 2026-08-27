import { getTranslations } from "next-intl/server";
import { DataExportManager } from "@/components/data-export-manager";

/**
 * "Data export & retention" page — first frontend consumer of
 * `GET /tenant/export` and `GET /tenant/retention` (Sprint 36, ADR-035),
 * LGPD Art. 9/18 II (right of access) and the automatic-retention policy.
 *
 * Visible to every authenticated user, same as `/budget`/`/approvals` — both
 * routes this page reads are `viewer`-and-above (ADR-035 Decision 1), no
 * `/config` role check needed here, unlike `/secrets` and `/admin`.
 *
 * `DELETE /tenant` (ADR-025, full tenant erasure) is deliberately not
 * surfaced on this page — see the PR description for why this was flagged
 * as a scope question rather than silently added as a destructive
 * account-deletion action.
 */
export default async function DataExportPage() {
  const t = await getTranslations("dataExportPage");

  return (
    <main className="flex-1 px-6 py-12 max-w-4xl mx-auto w-full flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t("subtitle")}</p>
      </div>
      <DataExportManager />
    </main>
  );
}
