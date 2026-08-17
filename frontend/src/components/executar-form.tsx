"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useRef, useState } from "react";

/**
 * PR 4 (Sprint 6, ADR-011) — the real "Executar" page: upload a file (or
 * paste a manual spec) + an optional business question, POST /runs, poll
 * GET /runs/{task_id}/status client-side until it's ready. Same shape as
 * `app.py::_tab_executar`'s enqueue-then-poll flow, just client-side
 * `setInterval` instead of `st.rerun()`.
 *
 * `getToken()` is called fresh on every request (submit *and* every poll
 * tick), not cached from submit-time — this is the whole point of Sprint 6
 * vs. Streamlit's static pasted-token flow: Clerk's client SDK silently
 * refreshes the session token, so a long-running analysis never hits an
 * expired-token dead end the way the old UI could.
 */

type TaskStatus = {
  state: string;
  ready: boolean;
  result: { run_id?: string; status?: string; error?: string | null } | null;
  error: string | null;
};

const POLL_INTERVAL_MS = 2000;

export function ExecutarForm() {
  const { getToken } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;

  const [file, setFile] = useState<File | null>(null);
  const [manualSpec, setManualSpec] = useState("");
  const [businessQuestion, setBusinessQuestion] = useState("");
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
        }
      } catch (err) {
        setError(String(err));
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
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-6 max-w-xl w-full">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium">Arquivo (CSV, Excel, JSON, PDF, DOCX)</span>
          <input
            type="file"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            disabled={submitting}
            className="border rounded p-2"
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium">
            Ou: spec manual (opcional se um arquivo foi anexado)
          </span>
          <textarea
            value={manualSpec}
            onChange={(e) => setManualSpec(e.target.value)}
            disabled={submitting}
            rows={3}
            placeholder='Read sales.csv, rename dt to date, filter active rows...'
            className="border rounded p-2 font-mono text-sm"
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium">Pergunta de negócio (opcional)</span>
          <textarea
            value={businessQuestion}
            onChange={(e) => setBusinessQuestion(e.target.value)}
            disabled={submitting}
            rows={2}
            placeholder="Quais produtos vendem mais?"
            className="border rounded p-2"
          />
        </label>

        <button
          type="submit"
          disabled={submitting}
          className="bg-black text-white dark:bg-white dark:text-black rounded px-4 py-2 font-medium disabled:opacity-50"
        >
          {submitting ? "Executando..." : "Analisar dados"}
        </button>
      </form>

      {error && <p className="text-red-600 text-sm">{error}</p>}

      {taskId && (
        <div className="border rounded p-4 flex flex-col gap-2 text-sm">
          <p>
            <span className="font-medium">Task:</span> {taskId}
          </p>
          <p>
            <span className="font-medium">Status:</span>{" "}
            {status?.state ?? "PENDING"}
          </p>
          {status?.ready && status.result?.status === "completed" && (
            <p className="text-green-600">
              ✅ Concluído — run_id: {status.result.run_id}
            </p>
          )}
          {status?.ready && status.error && (
            <p className="text-red-600">❌ {status.error}</p>
          )}
        </div>
      )}
    </div>
  );
}
