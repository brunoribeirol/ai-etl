import { PipelinesManager } from "@/components/pipelines-manager";

/**
 * "Pipelines" page (Sprint 13, ADR-016) — create/pause/resume/edit a saved
 * (recurring) pipeline. Distinct from "/" (avulso `POST /runs`, unchanged).
 */
export default function PipelinesPage() {
  return (
    <main className="flex-1 px-6 py-12 max-w-4xl mx-auto w-full flex flex-col gap-6 items-center">
      <div className="w-full max-w-2xl">
        <h1 className="text-2xl font-semibold tracking-tight">Pipelines agendados</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Crie um pipeline uma vez e deixe-o rodar sozinho no horário configurado.
        </p>
      </div>
      <PipelinesManager />
    </main>
  );
}
