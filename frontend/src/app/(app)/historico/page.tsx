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

/** True when `spec` looks like a file path rather than a natural-language
 * business question (e.g. `"runs/uploads/926ed592042f.csv"`). */
function isFilePath(spec: string): boolean {
  return spec.includes("/") && /\.[a-zA-Z0-9]{1,8}$/.test(spec);
}

/** Human-readable title for a run row. A file-path spec becomes "File:
 * <basename>"; a natural-language spec (business question) is shown as-is;
 * an empty spec falls back to the formatted run timestamp. The raw
 * `run_id`/`spec` stay available underneath for technical cross-referencing
 * — this only picks what's shown first. */
function runTitle(
  run: Pick<RunSummary, "spec" | "timestamp">,
  fileLabel: string,
): string {
  if (run.spec && isFilePath(run.spec)) {
    const filename = run.spec.split("/").pop() || run.spec;
    return fileLabel.replace("{name}", filename);
  }
  if (run.spec) {
    return run.spec;
  }
  return new Date(run.timestamp).toLocaleString();
}

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
                      <span className="text-sm truncate max-w-xs" title={run.run_id}>
                        {runTitle(run, t("runTitleFile"))}
                      </span>
                      <span className="font-mono text-[10px] text-muted-foreground/70 truncate max-w-xs">
                        {run.run_id}
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
