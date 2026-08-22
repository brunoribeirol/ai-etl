import { OnboardingChecklist } from "@/components/onboarding-checklist";
import { OnboardingWizard } from "@/components/onboarding-wizard";

/**
 * "Começar" page (Sprint 26, ADR-027) — guided first-run flow, additive to
 * (not a replacement for) `/` (`ExecutarForm`), `/pipelines`, and
 * `/resumo`. See the ADR for why this route exists separately rather than
 * gating those existing routes on activation status.
 */
export default function ComecarPage() {
  return (
    <main className="flex-1 flex flex-col items-center px-6 py-16 gap-10">
      <div className="flex flex-col items-center gap-2 text-center max-w-lg">
        <h1 className="text-2xl font-semibold tracking-tight">Comece em poucos minutos</h1>
        <p className="text-sm text-muted-foreground">
          Conecte uma fonte, descreva o que você quer e veja o resultado — sem precisar de
          instrução prévia.
        </p>
      </div>
      <div className="flex flex-col lg:flex-row gap-6 w-full max-w-4xl items-start justify-center">
        <div className="flex-1 flex flex-col items-center w-full">
          <OnboardingWizard />
        </div>
        <div className="w-full lg:w-72 shrink-0">
          <OnboardingChecklist />
        </div>
      </div>
    </main>
  );
}
