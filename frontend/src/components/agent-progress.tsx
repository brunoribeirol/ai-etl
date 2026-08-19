"use client";

import { motion } from "motion/react";
import { CheckCircle2, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { PHASES, stageToPhase } from "@/lib/agents";

/**
 * Sprint 7 — live per-phase progress for a running task, fed by
 * `GET /runs/{task_id}/status`'s `meta` field (`{stage, message}`), itself
 * fed by `pipeline_service.py`'s existing `progress_callback` hook now wired
 * to Celery's `update_state` (`services/execution_queue.py`). `stage` is
 * coarse ("silver" | "planner" | "gold:0" | "science:1_repair" | "advisor"),
 * `message` is the rich human-readable string the backend already formats —
 * this component just highlights which of the 4 macro-phases is current and
 * shows that message underneath, rather than trying to parse per-node detail
 * out of free text.
 */
export function AgentProgress({
  meta,
  done,
}: {
  meta: { stage: string; message: string } | null;
  done: boolean;
}) {
  const currentPhase = meta ? stageToPhase(meta.stage) : null;
  const currentIndex = currentPhase ? PHASES.findIndex((p) => p.key === currentPhase) : -1;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-1">
        {PHASES.map((phase, i) => {
          const isDone = done || i < currentIndex;
          const isActive = !done && i === currentIndex;
          return (
            <div key={phase.key} className="flex items-center gap-1 flex-1 last:flex-none">
              <div className="flex flex-col items-center gap-1.5 shrink-0">
                {isDone ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                ) : isActive ? (
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                ) : (
                  <div className="h-4 w-4 rounded-full border border-border" />
                )}
                <span
                  className={cn(
                    "text-[0.7rem] whitespace-nowrap",
                    isActive ? "text-foreground" : "text-muted-foreground"
                  )}
                >
                  {phase.label}
                </span>
              </div>
              {i < PHASES.length - 1 && (
                <div
                  className={cn(
                    "h-px flex-1 mb-4",
                    isDone ? "bg-emerald-400/50" : "bg-border"
                  )}
                />
              )}
            </div>
          );
        })}
      </div>

      {meta?.message && !done && (
        <motion.p
          key={meta.message}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="text-xs text-muted-foreground font-mono truncate"
        >
          {meta.message.replace(/\*\*/g, "")}
        </motion.p>
      )}
    </div>
  );
}
