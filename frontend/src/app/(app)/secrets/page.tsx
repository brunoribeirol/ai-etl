import { getTranslations } from "next-intl/server";
import { SecretsManager } from "@/components/secrets-manager";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { apiFetch } from "@/lib/api";
import type { ApiConfig } from "@/lib/types";

/**
 * "Secrets" page — first frontend consumer of `api/routers/secrets.py`
 * (Sprint 19, ADR-022), tenant-scoped external-source credential storage
 * that shipped 100% backend with no UI until now.
 *
 * Defense in depth, same pattern `(app)/admin/page.tsx` established:
 * `(app)/layout.tsx` only *shows* the nav link to a caller whose `/config`
 * role isn't `"viewer"` — that's cosmetic, so this page independently
 * re-checks the same `/config` role server-side before rendering anything
 * real. Every actual data call `<SecretsManager>` makes still hits a
 * `require_role("editor")`-gated route on its own regardless — this check
 * is a friendlier failure mode (a clear message instead of a stream of
 * per-request 403 toasts), not the security boundary itself. `"editor"` and
 * `"admin"` both pass here since `require_role("editor")` accepts either
 * (`api/deps.py::_ROLE_RANK`).
 */
export default async function SecretsPage() {
  const t = await getTranslations("secretsPage");

  let role: string | null = null;
  try {
    role = (await apiFetch<ApiConfig>("/config")).role;
  } catch {
    role = null;
  }

  if (role !== "editor" && role !== "admin") {
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
      <SecretsManager />
    </main>
  );
}
