"use client";

import { FileSpreadsheet, Loader2, UploadCloud } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";
import { ExecutarForm } from "@/components/executar-form";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const EXAMPLE_CSV_URL = "/examples/sample-sales.csv";

/**
 * Sprint 26 (ADR-027) — guided first-run flow. Step 1 picks a source
 * (example dataset, fetched client-side and handed to `ExecutarForm` as a
 * real `File`; or "upload your own", which just renders the plain form).
 * Once a source is chosen, `ExecutarForm` is mounted for the first time
 * with the right `initialFile`/`initialBusinessQuestion` — it owns every
 * following step (spec, submit, poll, result) unchanged from `/`, so this
 * wizard never reimplements upload/poll/result logic.
 */
export function OnboardingWizard() {
  const t = useTranslations("onboardingWizard");
  const [ready, setReady] = useState(false);
  const [initialFile, setInitialFile] = useState<File | null>(null);
  const [initialBusinessQuestion, setInitialBusinessQuestion] = useState("");
  const [loadingExample, setLoadingExample] = useState(false);

  async function useExample() {
    setLoadingExample(true);
    try {
      const response = await fetch(EXAMPLE_CSV_URL);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const file = new File([blob], "sample-sales.csv", { type: "text/csv" });
      setInitialFile(file);
      setInitialBusinessQuestion(t("exampleQuestion"));
      setReady(true);
    } catch (err) {
      toast.error(t("exampleLoadError", { error: String(err) }));
    } finally {
      setLoadingExample(false);
    }
  }

  function useOwnFile() {
    setInitialFile(null);
    setInitialBusinessQuestion("");
    setReady(true);
  }

  if (ready) {
    return <ExecutarForm initialFile={initialFile} initialBusinessQuestion={initialBusinessQuestion} />;
  }

  return (
    <Card className="max-w-xl w-full">
      <CardContent className="flex flex-col gap-4">
        <div>
          <span className="text-sm font-medium">{t("step1Title")}</span>
          <p className="text-xs text-muted-foreground mt-1">{t("step1Hint")}</p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Button
            type="button"
            variant="default"
            className="h-auto flex-col gap-2 py-4"
            onClick={useExample}
            disabled={loadingExample}
          >
            {loadingExample ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <FileSpreadsheet className="h-5 w-5" />
            )}
            <span>{t("useExample")}</span>
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-auto flex-col gap-2 py-4"
            onClick={useOwnFile}
            disabled={loadingExample}
          >
            <UploadCloud className="h-5 w-5" />
            <span>{t("useOwnFile")}</span>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
