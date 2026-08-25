import { getTranslations } from "next-intl/server";
import { AdminManager } from "@/components/admin-manager";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { apiFetch } from "@/lib/api";
import type { ApiConfig } from "@/lib/types";

/**
 * "Admin" page (Wave 6, 2026-08-25 admin panel/approval-gate UI plan) — first
 * frontend consumer of `api/routers/admin.py` (Sprint 31, ADR-032), which was
 * previously never wired to any UI at all.
 *
 * Defense in depth: `(app)/layout.tsx` only *shows* the nav link to a caller
 * whose `/config` role is `"admin"` — that's cosmetic, so this page
 * independently re-checks the same `/config` role server-side before
 * rendering anything real. Every actual data call `<AdminManager>` makes
 * still hits a `require_admin`-gated route on its own regardless — this
 * check is a friendlier failure mode (a clear message instead of a stream of
 * per-request 403 toasts), not the security boundary itself.
 */
export default async function AdminPage() {
  const t = await getTranslations("adminPage");

  let role: string | null = null;
  try {
    role = (await apiFetch<ApiConfig>("/config")).role;
  } catch {
    role = null;
  }

  if (role !== "admin") {
    return (
      <main className="flex-1 px-6 py-12 max-w-2xl mx-auto w-full">
        <Alert variant="destructive">
          <AlertDescription>{t("noPermission")}</AlertDescription>
        </Alert>
      </main>
    );
  }

  return (
    <main className="flex-1 px-6 py-12 max-w-4xl mx-auto w-full flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t("subtitle")}</p>
      </div>
      <AdminManager />
    </main>
  );
}
