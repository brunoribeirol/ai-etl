import { ExecutarForm } from "@/components/executar-form";

/**
 * "Executar" page (Sprint 6, PR 4, ADR-011) — replaces app.py's
 * `_tab_executar`. The auth-loop smoke test from PR 3 (a raw `GET /runs`
 * JSON dump) is gone now that there's a real page to render; auth itself is
 * unchanged — `middleware.ts` still gates this whole app via Clerk.
 */
export default function Home() {
  return (
    <main className="p-8 flex flex-col items-center gap-6">
      <h1 className="text-xl font-semibold">AI-ETL — Executar</h1>
      <ExecutarForm />
    </main>
  );
}
