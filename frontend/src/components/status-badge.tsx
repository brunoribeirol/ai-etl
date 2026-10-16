import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Sprint 7 redesign (ADR-011 surface, no API contract change) — one status
 * vocabulary shared by the "Run" poll result and the History table,
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
  awaiting_approval: "bg-amber-500/15 text-amber-400 border-amber-500/30",
};

// The API status values mapped to the labels of the `status` messages, so the badge speaks the
// interface's language instead of showing the raw value ("awaiting_approval"). Unknown values
// still render as they come.
const STATUS_LABEL_KEYS: Record<string, string> = {
  completed: "completed",
  success: "completed",
  running: "running",
  started: "running",
  pending: "pending",
  failed: "failed",
  failure: "failed",
  error: "failed",
  awaiting_approval: "awaitingApproval",
};

export function StatusBadge({ status }: { status: string }) {
  const t = useTranslations("status");
  const key = status.toLowerCase();
  const labelKey = STATUS_LABEL_KEYS[key];
  return (
    <Badge
      variant="outline"
      className={cn(
        "font-medium",
        !labelKey && "capitalize",
        STATUS_STYLES[key] ?? "text-muted-foreground",
      )}
    >
      {labelKey ? t(labelKey) : status}
    </Badge>
  );
}
