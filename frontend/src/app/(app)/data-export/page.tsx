import { getTranslations } from "next-intl/server";
import { DataExportManager } from "@/components/data-export-manager";
import { apiFetch } from "@/lib/api";
import type { ApiConfig } from "@/lib/types";

/**
 * "Data export & retention" page — first frontend consumer of
 * `GET /tenant/export`, `GET`/`PATCH /tenant/retention`, and `DELETE /tenant`
 * (Sprint 24 ADR-025, Sprint 36 ADR-035) — the 3 LGPD/GDPR data-subject
 * rights this project's backend already supported with zero UI: export
 * (Art. 9/18 II), retention (automatic purge window, opt-in), and erasure
 * (Art. 18 VI / GDPR Art. 17). All three were deliberately included together
 * per the owner's explicit decision (2026-08-27) — an earlier draft of this
 * page shipped export/retention-display only and flagged erasure + editing
 * the retention window as open scope questions rather than deciding them
 * unilaterally.
 *
 * `GET /tenant/export` and `GET /tenant/retention` are `viewer`-and-above
 * (ADR-035 Decision 1); `PATCH /tenant/retention` and `DELETE /tenant` are
 * `editor`-only (ADR-025/ADR-035). Role is read here (server-side `/config`,
 * same cosmetic pattern `(app)/layout.tsx`/`budget/page.tsx` use) purely to
 * decide whether the edit/delete controls render — both routes
 * independently re-enforce the role server-side regardless of what this
 * page shows a `viewer` caller.
 */
export default async function DataExportPage() {
  const t = await getTranslations("dataExportPage");

  let canEdit = false;
  try {
    const config = await apiFetch<ApiConfig>("/config");
    canEdit = config.role === "editor" || config.role === "admin";
  } catch {
    canEdit = false;
  }

  return (
    <main className="flex-1 px-6 py-12 max-w-4xl mx-auto w-full flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t("subtitle")}</p>
      </div>
      <DataExportManager canEdit={canEdit} />
    </main>
  );
}
