import { getTranslations } from "next-intl/server";
import { BudgetManager } from "@/components/budget-manager";
import { apiFetch } from "@/lib/api";
import type { ApiConfig } from "@/lib/types";

/**
 * "Budget" page: first frontend consumer of `api/routers/budget.py`
 * (Sprint 29, ADR-019) as a tenant self-service surface: the tenant's own
 * monthly spend cap, distinct from `/admin`'s read-only cross-tenant budget
 * lookup (`GET /admin/tenants/{id}/budget`), which stays admin-only.
 *
 * Visible to every authenticated user, same as `/approvals`: `GET /budget`
 * has no role requirement, only `PATCH /budget` does. Role is read here
 * (server-side `/config`, same cosmetic pattern `(app)/layout.tsx` uses for
 * the admin nav link) purely to decide whether `<BudgetManager>` renders the
 * edit form; `PATCH /budget` independently enforces
 * `require_role("editor")` server-side regardless of what this page shows.
 */
export default async function BudgetPage() {
  const t = await getTranslations("budgetPage");

  let canEdit = false;
  try {
    const config = await apiFetch<ApiConfig>("/config");
    canEdit = config.role === "editor" || config.role === "admin";
  } catch {
    canEdit = false;
  }

  return (
    <main className="flex-1 px-4 sm:px-6 py-10 sm:py-14 max-w-5xl mx-auto w-full flex flex-col gap-6">
      <div>
        <h1 className="font-serif text-4xl leading-tight tracking-tight text-balance sm:text-5xl">{t("title")}</h1>
        <p className="mt-3 max-w-2xl text-muted-foreground">{t("subtitle")}</p>
      </div>
      <BudgetManager canEdit={canEdit} />
    </main>
  );
}
