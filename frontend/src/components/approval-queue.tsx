"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { CheckCircle2, ChevronDown, ChevronRight, Inbox, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAuthedFetch } from "@/lib/authed-fetch";
import type { FullResult, PendingApproval } from "@/lib/types";

type ConfirmState = { runId: string; action: "approve" | "reject" } | null;

/**
 * "Aprovações" queue (Wave 6, 2026-08-25 admin panel/approval-gate UI plan)
 * — first frontend consumer of the Sprint 27 (ADR-028) approval-gate routes,
 * which shipped with an explicit "Frontend is out of scope for this sprint"
 * note. Visible to every authenticated user (this app makes no viewer/editor
 * UI distinction anywhere today — see `pipelines-manager.tsx`); `approve`/
 * `reject` themselves are `editor`-gated server-side, a `viewer` caller gets
 * a 403 surfaced via the same toast-on-error path every mutation here uses.
 *
 * Only a saved/scheduled pipeline can ever be gated (an avulso run always has
 * a human watching synchronously) — see `run_silver_pipeline`'s docstring.
 *
 * Expanding a row lazily fetches `GET /runs/{run_id}` for the full context
 * ADR-028 says an operator needs before approving: `load_preview` (what
 * would actually be written), `quality_report`, and any non-"ok"
 * `sanity_check` already computed on the Gold/Science sub-tasks — not just
 * the write diff.
 */
export function ApprovalQueue() {
  const t = useTranslations("approvalQueue");
  const authedFetch = useAuthedFetch();

  const [pending, setPending] = useState<PendingApproval[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FullResult | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [confirming, setConfirming] = useState<ConfirmState>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const loadPending = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPending(await authedFetch<PendingApproval[]>("/runs/pending-approval"));
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
    loadPending();
  }, [loadPending]);

  async function toggleExpand(runId: string) {
    if (expandedRunId === runId) {
      setExpandedRunId(null);
      return;
    }
    setExpandedRunId(runId);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    try {
      setDetail(await authedFetch<FullResult>(`/runs/${runId}`));
    } catch (err) {
      setDetailError(String(err instanceof Error ? err.message : err));
    } finally {
      setDetailLoading(false);
    }
  }

  function startConfirm(runId: string, action: "approve" | "reject") {
    setConfirming({ runId, action });
    setRejectReason("");
  }

  function cancelConfirm() {
    setConfirming(null);
    setRejectReason("");
  }

  async function submitDecision() {
    if (!confirming) return;
    const { runId, action } = confirming;
    setSubmitting(true);
    try {
      if (action === "approve") {
        await authedFetch(`/runs/${runId}/approve`, { method: "POST" });
        toast.success(t("toastApproved"));
      } else {
        await authedFetch(`/runs/${runId}/reject`, {
          method: "POST",
          body: JSON.stringify({ reason: rejectReason }),
        });
        toast.success(t("toastRejected"));
      }
      setConfirming(null);
      setRejectReason("");
      if (expandedRunId === runId) setExpandedRunId(null);
      await loadPending();
    } catch (err) {
      toast.error(String(err instanceof Error ? err.message : err));
    } finally {
      setSubmitting(false);
    }
  }

  if (error) {
    return (
      <p className="text-destructive text-sm" role="alert">
        {error}
      </p>
    );
  }

  if (!loading && pending.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground border border-dashed rounded-xl">
        <Inbox className="h-6 w-6" />
        <p className="text-sm">{t("emptyState")}</p>
      </div>
    );
  }

  return (
    <div className="border border-border/60 rounded-xl overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8" />
            <TableHead>{t("columnPipeline")}</TableHead>
            <TableHead>{t("columnSpec")}</TableHead>
            <TableHead className="text-right">{t("columnWhen")}</TableHead>
            <TableHead className="text-right">{t("columnActions")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {pending.map((run) => {
            const isExpanded = expandedRunId === run.run_id;
            const isConfirmingThis = confirming?.runId === run.run_id;
            return (
              <Fragment key={run.run_id}>
                <TableRow className="cursor-pointer">
                  <TableCell className="p-0 pl-2">
                    <button
                      type="button"
                      onClick={() => toggleExpand(run.run_id)}
                      aria-label={t("toggleDetail")}
                    >
                      {isExpanded ? (
                        <ChevronDown className="h-4 w-4" aria-hidden="true" />
                      ) : (
                        <ChevronRight className="h-4 w-4" aria-hidden="true" />
                      )}
                    </button>
                  </TableCell>
                  <TableCell className="text-sm">
                    {run.pipeline_name ?? t("unnamedPipeline")}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground truncate max-w-xs">
                    {run.spec}
                  </TableCell>
                  <TableCell className="text-right text-xs text-muted-foreground">
                    {new Date(run.timestamp).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={submitting}
                        onClick={() =>
                          isConfirmingThis && confirming?.action === "approve"
                            ? submitDecision()
                            : startConfirm(run.run_id, "approve")
                        }
                      >
                        <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                        {isConfirmingThis && confirming?.action === "approve"
                          ? t("confirmApprove")
                          : t("approve")}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={submitting}
                        onClick={() =>
                          isConfirmingThis && confirming?.action === "reject"
                            ? submitDecision()
                            : startConfirm(run.run_id, "reject")
                        }
                      >
                        <XCircle className="h-4 w-4" aria-hidden="true" />
                        {isConfirmingThis && confirming?.action === "reject"
                          ? t("confirmReject")
                          : t("reject")}
                      </Button>
                      {isConfirmingThis && (
                        <Button size="sm" variant="ghost" disabled={submitting} onClick={cancelConfirm}>
                          {t("cancel")}
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>

                {isConfirmingThis && confirming?.action === "reject" && (
                  <TableRow key={`${run.run_id}-reject-reason`}>
                    <TableCell colSpan={5} className="bg-muted/30">
                      <Textarea
                        value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                        placeholder={t("reasonPlaceholder")}
                        rows={2}
                        className="text-sm"
                        disabled={submitting}
                      />
                    </TableCell>
                  </TableRow>
                )}

                {isExpanded && (
                  <TableRow key={`${run.run_id}-detail`}>
                    <TableCell colSpan={5} className="bg-muted/30">
                      <ApprovalDetail loading={detailLoading} error={detailError} result={detail} />
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function ApprovalDetail({
  loading,
  error,
  result,
}: {
  loading: boolean;
  error: string | null;
  result: FullResult | null;
}) {
  const t = useTranslations("approvalQueue");

  if (loading) {
    return <p className="text-xs text-muted-foreground py-2">{t("loadingDetail")}</p>;
  }
  if (error) {
    return (
      <p className="text-destructive text-xs py-2" role="alert">
        {error}
      </p>
    );
  }
  if (!result) return null;

  const preview = result.state.load_preview;
  const quality = result.state.quality_report;
  const warnings = [...result.gold, ...result.science].flatMap(
    (entry) => entry.sanity_check?.checks.filter((c) => c.severity !== "ok") ?? [],
  );

  return (
    <div className="flex flex-col gap-3 py-3 text-sm">
      {preview && (
        <Card>
          <CardContent className="flex flex-col gap-1 text-xs">
            <span className="font-medium text-sm">{t("previewHeading")}</span>
            <span>
              {t("previewDestination")}: <span className="font-mono">{preview.destination}</span>
            </span>
            <span>
              {t("previewRows")}: <span className="font-mono">{preview.would_write_rows}</span>
            </span>
            {preview.existing && (
              <span className="text-muted-foreground">
                {t("previewExisting")}:{" "}
                {Object.entries(preview.existing)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(", ")}
              </span>
            )}
          </CardContent>
        </Card>
      )}

      {quality && (
        <div className="text-xs text-muted-foreground">
          {t("qualitySummary")}: {quality.summary ?? "—"}
        </div>
      )}

      {warnings.length > 0 && (
        <div className="flex flex-col gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-500">
          <span className="font-medium">{t("warningsHeading")}</span>
          {warnings.map((w, i) => (
            <span key={i}>{w.detail}</span>
          ))}
        </div>
      )}
    </div>
  );
}
