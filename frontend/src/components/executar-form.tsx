"use client";

import { useAuth } from "@clerk/nextjs";
import { AnimatePresence, motion } from "motion/react";
import { Loader2, UploadCloud } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AgentProgress } from "@/components/agent-progress";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { StatusBadge } from "@/components/status-badge";
import type { TaskStatus } from "@/lib/types";

/**
 * PR 4 (Sprint 6, ADR-011), restyled in Sprint 7 — the API contract, polling
 * cadence, and fresh-per-request `getToken()` behavior are all unchanged
 * (see the original rationale below); only the presentation layer changed.
 *
 * `getToken()` is called fresh on every request (submit *and* every poll
 * tick), not cached from submit-time — this is the whole point of Sprint 6
 * vs. Streamlit's static pasted-token flow: Clerk's client SDK silently
 * refreshes the session token, so a long-running analysis never hits an
 * expired-token dead end the way the old UI could.
 */

const POLL_INTERVAL_MS = 2000;

type ExecutarFormProps = {
  /**
   * Sprint 26 (ADR-027) — the `/comecar` guided onboarding flow reuses this
   * form instead of reimplementing upload/poll/result logic. Both props are
   * optional and default to the original empty state, so `/` (`page.tsx`)
   * renders `<ExecutarForm />` unchanged. Only meant to be set once, at
   * mount — the onboarding page only renders this component after its
   * example file (if any) has finished loading.
   */
  initialFile?: File | null;
  initialBusinessQuestion?: string;
};

export function ExecutarForm({
  initialFile = null,
  initialBusinessQuestion = "",
}: ExecutarFormProps = {}) {
  const { getToken } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;

  const [file, setFile] = useState<File | null>(initialFile);
  const [manualSpec, setManualSpec] = useState("");
  const [businessQuestion, setBusinessQuestion] = useState(initialBusinessQuestion);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<TaskStatus | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, []);

  function pollStatus(id: string) {
    if (pollTimer.current) clearInterval(pollTimer.current);
    pollTimer.current = setInterval(async () => {
      try {
        const token = await getToken();
        const response = await fetch(`${apiUrl}/runs/${id}/status`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data: TaskStatus = await response.json();
        setStatus(data);
        if (data.ready) {
          if (pollTimer.current) clearInterval(pollTimer.current);
          setSubmitting(false);
          if (data.result?.status === "completed") {
            toast.success("Análise concluída.");
          } else if (data.error) {
            toast.error(data.error);
          }
        }
      } catch (err) {
        setError(String(err));
        toast.error(String(err));
        if (pollTimer.current) clearInterval(pollTimer.current);
        setSubmitting(false);
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!apiUrl) {
      setError("NEXT_PUBLIC_API_URL is not configured.");
      return;
    }
    if (!file && !manualSpec.trim()) {
      setError("Anexe um arquivo ou escreva um spec manual.");
      return;
    }

    setError(null);
    setStatus(null);
    setTaskId(null);
    setSubmitting(true);

    try {
      const token = await getToken();
      const formData = new FormData();
      if (file) formData.append("file", file);
      if (manualSpec.trim()) formData.append("manual_spec", manualSpec.trim());
      formData.append("business_question", businessQuestion);

      const response = await fetch(`${apiUrl}/runs`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail ?? `HTTP ${response.status}`);
      }

      const { task_id }: { task_id: string } = await response.json();
      setTaskId(task_id);
      pollStatus(task_id);
    } catch (err) {
      setError(String(err));
      toast.error(String(err));
      setSubmitting(false);
    }
  }

  const displayStatus = status?.ready
    ? (status.result?.status ?? (status.error ? "failed" : "completed"))
    : (status?.state ?? (submitting ? "pending" : null));

  return (
    <div className="flex flex-col gap-6 max-w-xl w-full">
      <Card>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <Label htmlFor="file">Arquivo (CSV, Excel, JSON, PDF, DOCX)</Label>
              <label
                htmlFor="file"
                className="flex items-center gap-3 border border-dashed rounded-md px-4 py-3 text-sm cursor-pointer hover:bg-accent/50 transition-colors"
              >
                <UploadCloud className="h-4 w-4 text-muted-foreground shrink-0" />
                <span className="truncate text-muted-foreground">
                  {file ? file.name : "Clique para selecionar um arquivo"}
                </span>
              </label>
              <Input
                id="file"
                type="file"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                disabled={submitting}
                className="hidden"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="manualSpec">
                Ou: spec manual{" "}
                <span className="text-muted-foreground font-normal">
                  (opcional se um arquivo foi anexado)
                </span>
              </Label>
              <Textarea
                id="manualSpec"
                value={manualSpec}
                onChange={(e) => setManualSpec(e.target.value)}
                disabled={submitting}
                rows={3}
                placeholder="Read sales.csv, rename dt to date, filter active rows..."
                className="font-mono text-sm"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="businessQuestion">Pergunta de negócio (opcional)</Label>
              <Textarea
                id="businessQuestion"
                value={businessQuestion}
                onChange={(e) => setBusinessQuestion(e.target.value)}
                disabled={submitting}
                rows={2}
                placeholder="Quais produtos vendem mais?"
              />
            </div>

            <Button type="submit" disabled={submitting} className="w-full">
              {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
              {submitting ? "Executando..." : "Analisar dados"}
            </Button>
          </form>
        </CardContent>
      </Card>

      {error && (
        <p className="text-destructive text-sm" role="alert">
          {error}
        </p>
      )}

      <AnimatePresence>
        {taskId && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <Card>
              <CardContent className="flex flex-col gap-3 text-sm">
                <div className="flex justify-between items-center">
                  <span className="text-muted-foreground">Task</span>
                  <span className="font-mono text-xs">{taskId}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-muted-foreground">Status</span>
                  {displayStatus && <StatusBadge status={displayStatus} />}
                </div>
                <AgentProgress meta={status?.meta ?? null} done={!!status?.ready} />
                {status?.ready && status.result?.status === "completed" && (
                  <p className="text-emerald-400">
                    ✅ Concluído —{" "}
                    <Link href={`/historico/${status.result.run_id}`} className="underline">
                      ver run {status.result.run_id}
                    </Link>
                  </p>
                )}
                {status?.ready && status.error && (
                  <p className="text-destructive">❌ {status.error}</p>
                )}
              </CardContent>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
