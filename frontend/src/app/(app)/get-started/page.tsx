import { getTranslations } from "next-intl/server";
import { OnboardingChecklist } from "@/components/onboarding-checklist";
import { OnboardingWizard } from "@/components/onboarding-wizard";

/**
 * "Get Started" page (Sprint 26, ADR-027) — guided first-run flow, additive to
 * (not a replacement for) `/` (`RunForm`), `/pipelines`, and
 * `/summary`. See the ADR for why this route exists separately rather than
 * gating those existing routes on activation status.
 *
 * Sprint 25 (ADR-036) — copy comes from `messages/{locale}.json`'s
 * `getStartedPage` namespace.
 */
export default async function GetStartedPage() {
  const t = await getTranslations("getStartedPage");
  return (
    <main className="flex-1 flex flex-col items-center px-6 py-16 gap-10">
      <div className="flex flex-col items-center gap-2 text-center max-w-lg">
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
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
