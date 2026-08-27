"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Download, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useAuthedFetch } from "@/lib/authed-fetch";
import type { RetentionPolicy, TenantExport } from "@/lib/types";

/**
 * "Data export & retention" page's data + interaction layer — first
 * frontend consumer of `GET /tenant/export` and `GET /tenant/retention`
 * (Sprint 36, ADR-035), LGPD Art. 9/18 II (right of access) and the
 * automatic-retention policy display. `DELETE /tenant` (ADR-025, full
 * tenant erasure) is deliberately NOT surfaced here — see the PR
 * description for why this is flagged as a scope question rather than
 * silently added.
 *
 * Both `GET /tenant/export` and `GET /tenant/retention` are `viewer`-and-
 * above (ADR-035 Decision 1) — no role gating needed on this page, same as
 * `budget-manager.tsx`. Setting/clearing the retention window
 * (`PATCH /tenant/retention`, `editor`-gated) is out of scope for this UI —
 * this page only displays the current policy.
 *
 * Export uses the same inline 2-click confirm `approval-queue.tsx`
 * established: an export can be a non-trivial payload (every run/analysis
 * row a tenant owns), so it earns one extra tap before the request fires,
 * same reasoning `budget-manager.tsx` applies to clearing a cap.
 *
 * There is no existing convention in this codebase for triggering a browser
 * file download from a fetched JSON response (checked for `Blob`/`download`
 * usage repo-wide) — this introduces one: fetch, wrap in a `Blob`, and click
 * a transient `<a download>` built from an object URL, revoked right after.
 */
export function DataExportManager() {
  const t = useTranslations("dataExportManager");
  const authedFetch = useAuthedFetch();

  const [policy, setPolicy] = useState<RetentionPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [confirmingExport, setConfirmingExport] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportStatus, setExportStatus] = useState<string | null>(null);

  const loadPolicy = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPolicy(await authedFetch<RetentionPolicy>("/tenant/retention"));
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setLoading(false);
    }
  }, [authedFetch]);

  useEffect(() => {
    // Intentional mount-time fetch, same established pattern as
    // pipelines-manager.tsx::loadPipelines.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadPolicy();
  }, [loadPolicy]);

  async function runExport() {
    setConfirmingExport(false);
    setExporting(true);
    setExportStatus(t("exportInProgress"));
    try {
      const data = await authedFetch<TenantExport>("/tenant/export");
      downloadJson(data, `tenant-export-${data.tenant_id}-${Date.now()}.json`);
      setExportStatus(t("exportDone"));
      toast.success(t("toastExported"));
    } catch (err) {
      setExportStatus(null);
      toast.error(String(err instanceof Error ? err.message : err));
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="flex flex-col gap-6 max-w-lg w-full">
      <Card>
        <CardContent className="flex flex-col gap-2 text-sm">
          <span className="text-xs text-muted-foreground">{t("retentionHeading")}</span>
          {error ? (
            <p className="text-destructive text-sm" role="alert">
              {error}
            </p>
          ) : (
            <span className="font-mono">
              {loading
                ? "…"
                : policy?.retention_days != null
                  ? t("retentionWindow", { days: policy.retention_days })
                  : t("retentionKeepForever")}
            </span>
          )}
          <p className="text-xs text-muted-foreground">{t("retentionExplainer")}</p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <h2 className="text-sm font-medium flex items-center gap-2">
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              {t("exportHeading")}
            </h2>
            <p className="text-xs text-muted-foreground">{t("exportExplainer")}</p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              disabled={exporting}
              onClick={() => (confirmingExport ? runExport() : setConfirmingExport(true))}
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              {confirmingExport ? t("confirmExport") : t("exportButton")}
            </Button>
            {confirmingExport && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={exporting}
                onClick={() => setConfirmingExport(false)}
              >
                {t("cancel")}
              </Button>
            )}
          </div>
          {exportStatus && (
            <p className="text-xs text-muted-foreground" role="status" aria-live="polite">
              {exportStatus}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/** Triggers a browser download of `data` as a pretty-printed JSON file named
 * `filename`, via a transient object-URL anchor — no existing helper for
 * this in the codebase (checked repo-wide for `Blob`/`download` usage), so
 * this is the first one. */
function downloadJson(data: unknown, filename: string): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}
