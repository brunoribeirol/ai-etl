"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Download, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuthedFetch } from "@/lib/authed-fetch";
import type { RetentionPolicy, TenantDeletionSummary, TenantExport } from "@/lib/types";

/**
 * "Data export & retention" page's data + interaction layer — first
 * frontend consumer of `GET /tenant/export`, `GET`/`PATCH /tenant/retention`,
 * and `DELETE /tenant` (Sprint 24 ADR-025, Sprint 36 ADR-035): the 3 LGPD/GDPR
 * data-subject rights this backend already supported with zero UI — export,
 * retention, and erasure. All three included per the owner's explicit
 * 2026-08-27 decision (an earlier draft flagged erasure and retention-editing
 * as open scope questions instead of deciding them unilaterally).
 *
 * `GET /tenant/export`/`GET /tenant/retention` are `viewer`-and-above
 * (ADR-035 Decision 1) — always rendered. `PATCH /tenant/retention` and
 * `DELETE /tenant` are `editor`-only — `canEdit` (server-side `/config` role,
 * same cosmetic pattern `budget-manager.tsx` uses) gates both sections;
 * either route independently re-enforces the role server-side regardless of
 * what this component shows a `viewer` caller.
 *
 * Export and retention-clearing use the same inline 2-click confirm
 * `approval-queue.tsx`/`budget-manager.tsx` established. `DELETE /tenant` is
 * a materially bigger action (irreversible full-account erasure, not just a
 * cleared setting) — the backend's own contract already specifies a
 * stronger confirmation than 2 clicks (`ADR-025 Decision 4`: the request
 * body requires `confirm: "DELETE"`, a literal string match) — this UI
 * mirrors that exactly with a text input that must read `DELETE` before the
 * button enables, rather than inventing a weaker client-side gate.
 *
 * There is no existing convention in this codebase for triggering a browser
 * file download from a fetched JSON response (checked for `Blob`/`download`
 * usage repo-wide) — this introduces one: fetch, wrap in a `Blob`, and click
 * a transient `<a download>` built from an object URL, revoked right after.
 */
export function DataExportManager({ canEdit }: { canEdit: boolean }) {
  const t = useTranslations("dataExportManager");
  const authedFetch = useAuthedFetch();

  const [policy, setPolicy] = useState<RetentionPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [confirmingExport, setConfirmingExport] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportStatus, setExportStatus] = useState<string | null>(null);

  const [retentionInput, setRetentionInput] = useState("");
  const [savingRetention, setSavingRetention] = useState(false);
  const [confirmingClearRetention, setConfirmingClearRetention] = useState(false);

  const [deleteConfirmText, setDeleteConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deletionSummary, setDeletionSummary] = useState<TenantDeletionSummary | null>(null);

  const loadPolicy = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await authedFetch<RetentionPolicy>("/tenant/retention");
      setPolicy(data);
      setRetentionInput(data.retention_days != null ? String(data.retention_days) : "");
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

  async function saveRetention(retentionDays: number | null) {
    setSavingRetention(true);
    try {
      const data = await authedFetch<RetentionPolicy>("/tenant/retention", {
        method: "PATCH",
        body: JSON.stringify({ retention_days: retentionDays }),
      });
      setPolicy(data);
      setRetentionInput(data.retention_days != null ? String(data.retention_days) : "");
      setConfirmingClearRetention(false);
      toast.success(retentionDays != null ? t("toastRetentionSaved") : t("toastRetentionCleared"));
    } catch (err) {
      toast.error(String(err instanceof Error ? err.message : err));
    } finally {
      setSavingRetention(false);
    }
  }

  function handleRetentionSubmit(event: React.FormEvent) {
    event.preventDefault();
    const parsed = Number(retentionInput);
    if (retentionInput.trim() === "" || !Number.isInteger(parsed) || parsed < 1) {
      toast.error(t("invalidRetention"));
      return;
    }
    saveRetention(parsed);
  }

  async function runDeletion() {
    setDeleting(true);
    try {
      const summary = await authedFetch<TenantDeletionSummary>("/tenant", {
        method: "DELETE",
        body: JSON.stringify({ confirm: "DELETE" }),
      });
      setDeletionSummary(summary);
      toast.success(t("toastDeleted"));
    } catch (err) {
      toast.error(String(err instanceof Error ? err.message : err));
    } finally {
      setDeleting(false);
    }
  }

  if (deletionSummary) {
    return (
      <Card>
        <CardContent className="flex flex-col gap-3 text-sm">
          <h2 className="text-sm font-medium">{t("deletionCompleteHeading")}</h2>
          <p className="text-xs text-muted-foreground">{t("deletionCompleteExplainer")}</p>
          <ul className="text-xs font-mono flex flex-col gap-0.5">
            <li>{t("deletionCountRuns", { count: deletionSummary.runs_deleted })}</li>
            <li>{t("deletionCountAnalysisRuns", { count: deletionSummary.analysis_runs_deleted })}</li>
            <li>{t("deletionCountSavedPipelines", { count: deletionSummary.saved_pipelines_deleted })}</li>
            <li>{t("deletionCountSecrets", { count: deletionSummary.secrets_deleted })}</li>
          </ul>
          <Link href="/" className="text-xs underline underline-offset-2 w-fit">
            {t("deletionCompleteBackLink")}
          </Link>
        </CardContent>
      </Card>
    );
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

      {canEdit && (
        <Card>
          <CardContent>
            <form onSubmit={handleRetentionSubmit} className="flex flex-col gap-4">
              <h2 className="text-sm font-medium">{t("retentionFormHeading")}</h2>
              <div className="flex flex-col gap-1.5 max-w-xs">
                <Label htmlFor="retentionInput">{t("retentionInputLabel")}</Label>
                <Input
                  id="retentionInput"
                  type="number"
                  min={1}
                  step="1"
                  inputMode="numeric"
                  value={retentionInput}
                  onChange={(e) => setRetentionInput(e.target.value)}
                  placeholder={t("retentionInputPlaceholder")}
                  disabled={savingRetention || loading}
                />
              </div>
              <div className="flex items-center gap-2">
                <Button type="submit" size="sm" disabled={savingRetention || loading}>
                  {t("save")}
                </Button>
                {policy?.retention_days != null && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={savingRetention || loading}
                    onClick={() =>
                      confirmingClearRetention ? saveRetention(null) : setConfirmingClearRetention(true)
                    }
                  >
                    {confirmingClearRetention ? t("confirmClearRetention") : t("clearRetention")}
                  </Button>
                )}
                {confirmingClearRetention && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={savingRetention}
                    onClick={() => setConfirmingClearRetention(false)}
                  >
                    {t("cancel")}
                  </Button>
                )}
              </div>
            </form>
          </CardContent>
        </Card>
      )}

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

      {canEdit && (
        <Card className="border-destructive/50">
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <h2 className="text-sm font-medium flex items-center gap-2 text-destructive">
                <Trash2 className="h-4 w-4" aria-hidden="true" />
                {t("deleteHeading")}
              </h2>
              <p className="text-xs text-muted-foreground">{t("deleteExplainer")}</p>
            </div>
            <div className="flex flex-col gap-1.5 max-w-xs">
              <Label htmlFor="deleteConfirmInput">{t("deleteConfirmLabel")}</Label>
              <Input
                id="deleteConfirmInput"
                value={deleteConfirmText}
                onChange={(e) => setDeleteConfirmText(e.target.value)}
                placeholder="DELETE"
                disabled={deleting}
                autoComplete="off"
              />
            </div>
            <Button
              type="button"
              size="sm"
              variant="destructive"
              disabled={deleteConfirmText !== "DELETE" || deleting}
              onClick={runDeletion}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              {t("deleteButton")}
            </Button>
          </CardContent>
        </Card>
      )}
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
