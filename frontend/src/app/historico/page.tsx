import Link from "next/link";
import { apiFetch } from "@/lib/api";
import type { RunSummary } from "@/lib/types";

/**
 * "Histórico" page (Sprint 6, PR 5, ADR-011) — replaces `app.py::_tab_historico`'s
 * list view. Server Component: fetches `GET /runs` once per request
 * (`cache: "no-store"` in `apiFetch`), same tenant-scoped query the Streamlit
 * History tab already used.
 */
export default async function Historico() {
  let runs: RunSummary[] = [];
  let error: string | null = null;

  try {
    runs = await apiFetch<RunSummary[]>("/runs");
  } catch (err) {
    error = String(err);
  }

  return (
    <main className="p-8 flex flex-col gap-6 max-w-3xl mx-auto w-full">
      <h1 className="text-xl font-semibold">Histórico</h1>

      {error && <p className="text-red-600 text-sm">{error}</p>}
      {!error && runs.length === 0 && (
        <p className="text-sm text-gray-500">Nenhum run ainda.</p>
      )}

      <ul className="flex flex-col gap-2">
        {runs.map((run) => (
          <li key={run.run_id}>
            <Link
              href={`/historico/${run.run_id}`}
              className="border rounded p-4 flex flex-col gap-1 hover:bg-gray-50 dark:hover:bg-gray-900"
            >
              <div className="flex justify-between text-sm gap-4">
                <span className="font-mono truncate">{run.run_id}</span>
                <span className="shrink-0">{run.status}</span>
              </div>
              <p className="text-sm text-gray-500 truncate">{run.spec}</p>
              <div className="flex gap-4 text-xs text-gray-400">
                <span>{new Date(run.timestamp).toLocaleString()}</span>
                {run.model_name && <span>{run.model_name}</span>}
                {run.cost_usd != null && <span>${run.cost_usd.toFixed(6)}</span>}
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
