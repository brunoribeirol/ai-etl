"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Inbox } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StatusBadge } from "@/components/status-badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAuthedFetch } from "@/lib/authed-fetch";
import type { AdminActionRecord, AdminTenantSummary, BudgetStatus, RunSummary } from "@/lib/types";

/**
 * `/admin` page's actual data + interaction layer (Wave 6, 2026-08-25 admin
 * panel/approval-gate UI plan). Client Component — every tab needs a fresh
 * per-request token (`useAuthedFetch`), same reasoning `pipelines-manager.tsx`
 * already established. The page component wrapping this one has already
 * verified `role === "admin"` server-side before rendering this at all; every
 * fetch below still independently hits a `require_admin`-gated route, so a
 * stale/cached role check here is never the only thing standing between a
 * non-admin and this data.
 *
 * Two tabs: "Audit log" (default — `GET /admin/audit-log`, the DoD's
 * "queryable" requirement made concrete) and "Query tenant" (`GET
 * /admin/tenants` for the picker, ADR-032's other 2 read-only routes for the
 * selected tenant's runs/budget). Clicking a `target_tenant_id` in the audit
 * log pivots into the tenant tab with that id pre-selected — the audit log
 * is the only real way to *discover* a tenant id in context today, since
 * `/admin/tenants` returns raw Clerk ids with no display name.
 */
export function AdminManager() {
  const t = useTranslations("adminManager");
  const authedFetch = useAuthedFetch();

  const [tab, setTab] = useState("audit");

  // --- Audit log tab ---
  const [auditLog, setAuditLog] = useState<AdminActionRecord[]>([]);
  const [auditLoading, setAuditLoading] = useState(true);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [actorFilter, setActorFilter] = useState("");
  const [targetTenantFilter, setTargetTenantFilter] = useState("");

  const loadAuditLog = useCallback(
    async (actorUserId: string, targetTenantId: string) => {
      setAuditLoading(true);
      setAuditError(null);
      try {
        const params = new URLSearchParams({ limit: "100" });
        if (actorUserId.trim()) params.set("actor_user_id", actorUserId.trim());
        if (targetTenantId.trim()) params.set("target_tenant_id", targetTenantId.trim());
        const data = await authedFetch<AdminActionRecord[]>(`/admin/audit-log?${params}`);
        setAuditLog(data);
      } catch (err) {
        setAuditError(String(err instanceof Error ? err.message : err));
      } finally {
        setAuditLoading(false);
      }
    },
    [authedFetch],
  );

  useEffect(() => {
    // Intentional mount-time fetch, same established pattern as
    // pipelines-manager.tsx::loadPipelines.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadAuditLog("", "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleFilterSubmit(event: React.FormEvent) {
    event.preventDefault();
    loadAuditLog(actorFilter, targetTenantFilter);
  }

  // --- Tenant lookup tab ---
  const [tenants, setTenants] = useState<AdminTenantSummary[]>([]);
  const [tenantsLoading, setTenantsLoading] = useState(true);
  const [selectedTenantId, setSelectedTenantId] = useState("");
  const [tenantRuns, setTenantRuns] = useState<RunSummary[]>([]);
  const [tenantBudget, setTenantBudget] = useState<BudgetStatus | null>(null);
  const [tenantLoading, setTenantLoading] = useState(false);
  const [tenantError, setTenantError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setTenantsLoading(true);
      try {
        setTenants(await authedFetch<AdminTenantSummary[]>("/admin/tenants"));
      } catch {
        // Non-fatal for this tab alone — the select just renders empty; the
        // audit-log tab (default) already surfaces auth/network failures.
      } finally {
        setTenantsLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadTenantDetail = useCallback(
    async (tenantId: string) => {
      if (!tenantId) return;
      setTenantLoading(true);
      setTenantError(null);
      try {
        const [runs, budget] = await Promise.all([
          authedFetch<RunSummary[]>(`/admin/tenants/${tenantId}/runs`),
          authedFetch<BudgetStatus>(`/admin/tenants/${tenantId}/budget`),
        ]);
        setTenantRuns(runs);
        setTenantBudget(budget);
      } catch (err) {
        setTenantError(String(err instanceof Error ? err.message : err));
      } finally {
        setTenantLoading(false);
      }
    },
    [authedFetch],
  );

  function handleTenantSelect(tenantId: string) {
    setSelectedTenantId(tenantId);
    loadTenantDetail(tenantId);
  }

  function pivotToTenant(tenantId: string) {
    setTab("tenant");
    handleTenantSelect(tenantId);
  }

  return (
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList>
        <TabsTrigger value="audit">{t("auditTab")}</TabsTrigger>
        <TabsTrigger value="tenant">{t("tenantTab")}</TabsTrigger>
      </TabsList>

      <TabsContent value="audit" className="flex flex-col gap-4 pt-4">
        <form onSubmit={handleFilterSubmit} className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="actorFilter">{t("actorFilterLabel")}</Label>
            <Input
              id="actorFilter"
              value={actorFilter}
              onChange={(e) => setActorFilter(e.target.value)}
              placeholder={t("actorFilterPlaceholder")}
              className="font-mono text-xs w-56"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="targetTenantFilter">{t("targetTenantFilterLabel")}</Label>
            <Input
              id="targetTenantFilter"
              value={targetTenantFilter}
              onChange={(e) => setTargetTenantFilter(e.target.value)}
              placeholder={t("targetTenantFilterPlaceholder")}
              className="font-mono text-xs w-56"
            />
          </div>
          <Button type="submit" size="sm" variant="outline">
            {t("filterButton")}
          </Button>
        </form>

        {auditError && (
          <p className="text-destructive text-sm" role="alert">
            {auditError}
          </p>
        )}
        {!auditLoading && !auditError && auditLog.length === 0 && (
          <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground border border-dashed rounded-xl">
            <Inbox className="h-6 w-6" />
            <p className="text-sm">{t("auditEmptyState")}</p>
          </div>
        )}
        {auditLog.length > 0 && (
          <div className="border border-border/60 rounded-xl overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("columnAction")}</TableHead>
                  <TableHead>{t("columnActor")}</TableHead>
                  <TableHead>{t("columnTargetTenant")}</TableHead>
                  <TableHead>{t("columnDetail")}</TableHead>
                  <TableHead className="text-right">{t("columnWhen")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {auditLog.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="font-mono text-xs">{entry.action}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {entry.actor_user_id}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {entry.target_tenant_id ? (
                        <button
                          type="button"
                          onClick={() => pivotToTenant(entry.target_tenant_id as string)}
                          className="text-primary hover:underline"
                        >
                          {entry.target_tenant_id}
                        </button>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground max-w-xs truncate">
                      {entry.detail ?? "—"}
                    </TableCell>
                    <TableCell className="text-right text-xs text-muted-foreground">
                      {new Date(entry.created_at).toLocaleString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </TabsContent>

      <TabsContent value="tenant" className="flex flex-col gap-4 pt-4">
        <div className="flex flex-col gap-1.5 max-w-sm">
          <Label htmlFor="tenantSelect">{t("tenantSelectLabel")}</Label>
          <select
            id="tenantSelect"
            value={selectedTenantId}
            onChange={(e) => handleTenantSelect(e.target.value)}
            disabled={tenantsLoading}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs outline-none disabled:opacity-50"
          >
            <option value="">{t("tenantSelectPlaceholder")}</option>
            {tenants.map((tenant) => (
              <option key={tenant.tenant_id} value={tenant.tenant_id}>
                {tenant.tenant_id} · {new Date(tenant.created_at).toLocaleDateString()}
              </option>
            ))}
          </select>
        </div>

        {tenantError && (
          <p className="text-destructive text-sm" role="alert">
            {tenantError}
          </p>
        )}

        {!tenantLoading && !tenantError && selectedTenantId && tenantBudget && (
          <Card>
            <CardContent className="flex flex-wrap gap-6 text-sm">
              <div className="flex flex-col gap-1">
                <span className="text-xs text-muted-foreground">{t("budgetCap")}</span>
                <span className="font-mono">
                  {tenantBudget.cap_usd != null ? `$${tenantBudget.cap_usd.toFixed(2)}` : "—"}
                </span>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-xs text-muted-foreground">{t("budgetSpent")}</span>
                <span className="font-mono">${tenantBudget.spent_usd.toFixed(2)}</span>
              </div>
              {tenantBudget.cap_usd != null && (
                <div className="flex flex-col gap-1">
                  <span className="text-xs text-muted-foreground">{t("budgetStatus")}</span>
                  <Badge
                    variant="outline"
                    className={
                      tenantBudget.exceeded
                        ? "bg-red-500/15 text-red-400 border-red-500/30"
                        : tenantBudget.near_limit
                          ? "bg-amber-500/15 text-amber-400 border-amber-500/30"
                          : "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
                    }
                  >
                    {tenantBudget.exceeded
                      ? t("budgetExceeded")
                      : tenantBudget.near_limit
                        ? t("budgetNearLimit")
                        : t("budgetOk")}
                  </Badge>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {!tenantLoading && !tenantError && selectedTenantId && tenantRuns.length === 0 && (
          <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground border border-dashed rounded-xl">
            <Inbox className="h-6 w-6" />
            <p className="text-sm">{t("tenantRunsEmptyState")}</p>
          </div>
        )}
        {tenantRuns.length > 0 && (
          <div className="border border-border/60 rounded-xl overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("columnRun")}</TableHead>
                  <TableHead>{t("columnStatus")}</TableHead>
                  <TableHead>{t("columnModel")}</TableHead>
                  <TableHead className="text-right">{t("columnCost")}</TableHead>
                  <TableHead className="text-right">{t("columnWhen")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tenantRuns.map((run) => (
                  <TableRow key={run.run_id}>
                    <TableCell className="font-mono text-xs truncate max-w-xs">
                      {run.run_id}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={run.status} />
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {run.model_name ?? "—"}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {run.cost_usd != null ? `$${run.cost_usd.toFixed(6)}` : "—"}
                    </TableCell>
                    <TableCell className="text-right text-xs text-muted-foreground">
                      {new Date(run.timestamp).toLocaleString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </TabsContent>
    </Tabs>
  );
}
