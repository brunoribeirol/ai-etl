import { getTranslations } from "next-intl/server";
import { CodeBlock } from "@/components/code-block";
import type { AnalysisEntry } from "@/lib/types";

/**
 * Sprint 7 — replaces `app.py`'s "Code" tab (originally in Portuguese). Silver's `transformation_code`
 * was already returned by `GET /runs/{run_id}` (just unused by the frontend);
 * Gold/Science `code` is a Sprint 7 backend addition (`audit/db.py`'s
 * `_serialize_analysis_result` — see PR description) so older runs saved
 * before this change simply won't have it (`code` is optional).
 */
export async function CodeTab({
  transformationCode,
  gold,
  science,
}: {
  transformationCode?: string;
  gold: AnalysisEntry[];
  science: AnalysisEntry[];
}) {
  const t = await getTranslations("codeTab");
  const hasAny = transformationCode || gold.some((g) => g.code) || science.some((s) => s.code);
  if (!hasAny) {
    return <p className="text-sm text-muted-foreground">{t("empty")}</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      {transformationCode && (
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{t("transformerHeading")}</h3>
          <CodeBlock code={transformationCode} filename="transform.py" />
        </section>
      )}
      {gold.map(
        (entry, i) =>
          entry.code && (
            <section key={`gold-code-${i}`} className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">
                {t("analystHeading", {
                  question: entry.task_question ?? t("analystFallback", { index: i + 1 }),
                })}
              </h3>
              <CodeBlock code={entry.code} filename={`gold_${i}.py`} />
            </section>
          )
      )}
      {science.map(
        (entry, i) =>
          entry.code && (
            <section key={`science-code-${i}`} className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">
                {t("scienceHeading", {
                  question: entry.task_question ?? t("scienceFallback", { index: i + 1 }),
                })}
              </h3>
              <CodeBlock code={entry.code} filename={`science_${i}.py`} />
            </section>
          )
      )}
    </div>
  );
}
