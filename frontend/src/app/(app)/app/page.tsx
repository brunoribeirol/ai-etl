import { ExecutarForm } from "@/components/executar-form";

/**
 * "Executar" page (Sprint 6, PR 4 → Sprint 7 redesign, ADR-011). Same route
 * and behavior — the API call and auth flow live entirely in
 * `<ExecutarForm />`, unchanged; this is layout/copy only.
 */
export default function Home() {
  return (
    <main className="flex-1 flex flex-col items-center px-6 py-16 gap-10">
      <div className="flex flex-col items-center gap-2 text-center max-w-lg">
        <h1 className="text-2xl font-semibold tracking-tight">Executar análise</h1>
        <p className="text-sm text-muted-foreground">
          Suba um arquivo ou escreva um spec manual, opcionalmente com uma pergunta
          de negócio. O pipeline agente cuida do resto.
        </p>
      </div>
      <ExecutarForm />
    </main>
  );
}
