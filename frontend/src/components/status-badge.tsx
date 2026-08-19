import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Sprint 7 redesign (ADR-011 surface, no API contract change) — one status
 * vocabulary shared by the "Executar" poll result and the Histórico table,
 * replacing plain-text status strings with a consistent color/label pair.
 * Statuses come straight from the API (`audit/db.py`/Celery task state) —
 * this only maps known values to a look, unknown values still render as-is.
 */
const STATUS_STYLES: Record<string, string> = {
  completed: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  success: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  running: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  started: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  pending: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  failed: "bg-red-500/15 text-red-400 border-red-500/30",
  failure: "bg-red-500/15 text-red-400 border-red-500/30",
  error: "bg-red-500/15 text-red-400 border-red-500/30",
};

export function StatusBadge({ status }: { status: string }) {
  const key = status.toLowerCase();
  return (
    <Badge
      variant="outline"
      className={cn("capitalize font-medium", STATUS_STYLES[key] ?? "text-muted-foreground")}
    >
      {status}
    </Badge>
  );
}
