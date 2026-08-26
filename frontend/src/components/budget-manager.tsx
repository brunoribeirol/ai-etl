"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuthedFetch } from "@/lib/authed-fetch";
import type { BudgetStatus } from "@/lib/types";

/**
 * "Budget" page's data + interaction layer — first frontend consumer of
 * `api/routers/budget.py` (Sprint 29, ADR-019) as a *self-service* surface:
 * `admin-manager.tsx` already renders a read-only `BudgetStatus` for the
 * admin-viewing-another-tenant case (`GET /admin/tenants/{id}/budget`); this
 * component is the tenant managing their own cap (`GET`/`PATCH /budget`),
 * reusing that same visual treatment (cap/spent/status badge + thresholds)
 * since it's the same response shape either way.
 *
 * `role` is passed down from the page (server-side `/config` read, same
 * cosmetic-only pattern `(app)/layout.tsx` uses for the admin nav link) —
 * only gates whether the edit form renders; `PATCH /budget` independently
 * enforces `require_role("editor")` server-side regardless of what this
 * component shows a `viewer` caller.
 *
 * Clearing the cap gets the same lightweight 2-click inline confirm
 * `approval-queue.tsx` established for its approve/reject actions — setting
 * a cap is a single click (reversible by just setting it again), but
 * clearing one removes a spending control a tenant may have relied on, so
 * it earns the one extra tap; still a plain button rather than a full
 * modal since — unlike approve/reject — nothing is written or lost, only a
 * limit is turned off.
 */
export function BudgetManager({ canEdit }: { canEdit: boolean }) {
  const t = useTranslations("budgetManager");
  const authedFetch = useAuthedFetch();

  const [status, setStatus] = useState<BudgetStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [capInput, setCapInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [confirmingClear, setConfirmingClear] = useState(false);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await authedFetch<BudgetStatus>("/budget");
      setStatus(data);
      setCapInput(data.cap_usd != null ? String(data.cap_usd) : "");
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
    loadStatus();
  }, [loadStatus]);

  async function saveCap(monthlyBudgetUsd: number | null) {
    setSubmitting(true);
    try {
      const data = await authedFetch<BudgetStatus>("/budget", {
        method: "PATCH",
        body: JSON.stringify({ monthly_budget_usd: monthlyBudgetUsd }),
      });
      setStatus(data);
      setCapInput(data.cap_usd != null ? String(data.cap_usd) : "");
      setConfirmingClear(false);
      toast.success(monthlyBudgetUsd != null ? t("toastSaved") : t("toastCleared"));
    } catch (err) {
      toast.error(String(err instanceof Error ? err.message : err));
    } finally {
      setSubmitting(false);
    }
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const parsed = Number(capInput);
    if (capInput.trim() === "" || Number.isNaN(parsed) || parsed < 0) {
      toast.error(t("invalidCap"));
      return;
    }
    saveCap(parsed);
  }

  if (error) {
    return (
      <p className="text-destructive text-sm" role="alert">
        {error}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6 max-w-lg w-full">
      <Card>
        <CardContent className="flex flex-wrap gap-6 text-sm">
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">{t("budgetCap")}</span>
            <span className="font-mono">
              {loading ? "…" : status?.cap_usd != null ? `$${status.cap_usd.toFixed(2)}` : t("noCap")}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">{t("budgetSpent")}</span>
            <span className="font-mono">
              {loading ? "…" : `$${(status?.spent_usd ?? 0).toFixed(2)}`}
            </span>
          </div>
          {!loading && status?.cap_usd != null && (
            <div className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">{t("budgetStatus")}</span>
              <Badge
                variant="outline"
                className={
                  status.exceeded
                    ? "bg-red-500/15 text-red-400 border-red-500/30"
                    : status.near_limit
                      ? "bg-amber-500/15 text-amber-400 border-amber-500/30"
                      : "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
                }
              >
                {status.exceeded ? t("budgetExceeded") : status.near_limit ? t("budgetNearLimit") : t("budgetOk")}
              </Badge>
            </div>
          )}
        </CardContent>
      </Card>

      {canEdit && (
        <Card>
          <CardContent>
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <h2 className="text-sm font-medium">{t("formHeading")}</h2>
              <div className="flex flex-col gap-1.5 max-w-xs">
                <Label htmlFor="capInput">{t("capInputLabel")}</Label>
                <Input
                  id="capInput"
                  type="number"
                  min={0}
                  step="0.01"
                  inputMode="decimal"
                  value={capInput}
                  onChange={(e) => setCapInput(e.target.value)}
                  placeholder={t("capInputPlaceholder")}
                  disabled={submitting || loading}
                />
              </div>
              <div className="flex gap-2">
                <Button type="submit" size="sm" disabled={submitting || loading}>
                  {t("save")}
                </Button>
                {status?.cap_usd != null && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={submitting || loading}
                    onClick={() =>
                      confirmingClear ? saveCap(null) : setConfirmingClear(true)
                    }
                  >
                    {confirmingClear ? t("confirmClear") : t("clear")}
                  </Button>
                )}
                {confirmingClear && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={submitting}
                    onClick={() => setConfirmingClear(false)}
                  >
                    {t("cancel")}
                  </Button>
                )}
              </div>
            </form>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
