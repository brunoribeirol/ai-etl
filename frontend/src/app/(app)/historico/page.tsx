import Link from "next/link";
import { Inbox } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { apiFetch } from "@/lib/api";
import type { RunSummary } from "@/lib/types";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/**
 * "Histórico" page (Sprint 6, PR 5 → Sprint 7 redesign, ADR-011). Same
 * server-side `GET /runs` fetch (`cache: "no-store"`, tenant-scoped) — the
 * card-list markup is replaced with a shadcn Table, same data, same link
 * target per row.
 */
export default async function Historico() {
  const t = await getTranslations("historicoPage");
  let runs: RunSummary[] = [];
  let error: string | null = null;

  try {
    runs = await apiFetch<RunSummary[]>("/runs");
  } catch (err) {
    error = String(err);
  }

  return (
    <main className="flex-1 px-6 py-12 max-w-4xl mx-auto w-full flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t("subtitle")}</p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!error && runs.length === 0 && (
        <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground border border-dashed rounded-xl">
          <Inbox className="h-6 w-6" />
          <p className="text-sm">{t("emptyState")}</p>
        </div>
      )}

      {!error && runs.length > 0 && (
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
              {runs.map((run) => (
                <TableRow key={run.run_id} className="cursor-pointer">
                  <TableCell className="p-0">
                    <Link
                      href={`/historico/${run.run_id}`}
                      className="flex flex-col gap-0.5 px-2 py-2"
                    >
                      <span className="font-mono text-xs">{run.run_id}</span>
                      <span className="text-xs text-muted-foreground truncate max-w-xs">
                        {run.spec}
                      </span>
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Link href={`/historico/${run.run_id}`} className="block">
                      <StatusBadge status={run.status} />
                    </Link>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    <Link href={`/historico/${run.run_id}`} className="block">
                      {run.model_name ?? "—"}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs">
                    <Link href={`/historico/${run.run_id}`} className="block">
                      {run.cost_usd != null ? `$${run.cost_usd.toFixed(6)}` : "—"}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground text-xs">
                    <Link href={`/historico/${run.run_id}`} className="block">
                      {new Date(run.timestamp).toLocaleString()}
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </main>
  );
}
