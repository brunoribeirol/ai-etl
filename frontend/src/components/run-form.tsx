"use client";

import { useAuth } from "@clerk/nextjs";
import { AnimatePresence, motion } from "motion/react";
import { Loader2, UploadCloud } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AgentProgress } from "@/components/agent-progress";
import { ModelPicker } from "@/components/model-picker";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { StatusBadge } from "@/components/status-badge";
import { splitErrorMessage } from "@/lib/error-message";
import { describeSubmitError } from "@/lib/form-error";
import { formatUsd } from "@/lib/format-number";
import type { CostEstimate, TaskStatus } from "@/lib/types";

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

/** Rows and columns of a CSV file, counted in the browser for `POST /runs/estimate`, which
 * needs the dataset's shape and never reads the data. The delimiter is the one the header
 * uses more of (comma or semicolon). Quoted fields that span lines are counted as more rows:
 * this feeds an estimate, not a validation. Other formats return null and get no estimate. */
async function csvShape(file: File): Promise<{ rows: number; cols: number } | null> {
  if (!/\.csv$/i.test(file.name)) return null;
  const lines = (await file.text()).split(/\r?\n/).filter((line) => line.trim() !== "");
  if (lines.length < 2) return null;
  const header = lines[0];
  const delimiter = (header.match(/;/g)?.length ?? 0) > (header.match(/,/g)?.length ?? 0) ? ";" : ",";
  return { rows: lines.length - 1, cols: header.split(delimiter).length };
}

type RunFormProps = {
  /**
   * Sprint 26 (ADR-027) — the `/get-started` guided onboarding flow reuses this
   * form instead of reimplementing upload/poll/result logic. Both props are
   * optional and default to the original empty state, so `/` (`page.tsx`)
   * renders `<RunForm />` unchanged. Only meant to be set once, at
   * mount — the onboarding page only renders this component after its
   * example file (if any) has finished loading.
   */
  initialFile?: File | null;
  initialBusinessQuestion?: string;
};

export function RunForm({
  initialFile = null,
  initialBusinessQuestion = "",
}: RunFormProps = {}) {
  const t = useTranslations("runForm");
  const locale = useLocale();
  const { getToken } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;

  const [file, setFile] = useState<File | null>(initialFile);
  // Wave 1 item 2 (2026-09-05 mega-audit remediation,
  // docs/work/2026-09-05-mega-audit-remediation-plan.md): a multi-sheet Excel
  // upload used to always silently run against sheet 0 for the preview, then
  // fail during the real pipeline execution with no explanation ("Excel
  // multi-sheet always fails"). `sheetOptions` is `null` until a `.xlsx`/`.xls`
  // file is selected and checked; it stays `null` for every other file type,
  // and becomes `[]`/`[oneSheet]` (no picker needed) or a real list (picker
  // required before submit) once `POST /runs/excel-sheets` answers.
  const [sheetOptions, setSheetOptions] = useState<string[] | null>(null);
  const [selectedSheet, setSelectedSheet] = useState<string>("");
  const [checkingSheets, setCheckingSheets] = useState(false);
  const [manualSpec, setManualSpec] = useState("");
  const [businessQuestion, setBusinessQuestion] = useState(initialBusinessQuestion);
  // One-off runs have no saved-pipeline id to write an LLM override to
  // immediately (unlike `pipelines-manager.tsx`'s `ModelPicker`, which PUTs
  // to `/pipelines/{id}/llm-config` on click) — the choice is only kept in
  // local state here and threaded into the `/runs` submit below.
  const [llmProvider, setLlmProvider] = useState<string | null>(null);
  const [llmModel, setLlmModel] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<TaskStatus | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 2026-10-16: the estimate existed only in the API (functional checklist row 6.3). Shown
  // for a CSV upload, before the run, for the model chosen or the default one.
  const [shape, setShape] = useState<{ rows: number; cols: number } | null>(null);
  // Kept with the inputs it was computed for, so a stale answer is never shown after the
  // file or the model changes (no reset inside the effect).
  const [estimate, setEstimate] = useState<{ key: string; value: CostEstimate } | null>(null);
  const estimateKey = shape ? `${shape.rows}x${shape.cols}|${llmProvider}|${llmModel}` : null;
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, []);

  useEffect(() => {
    if (!initialFile) return;
    csvShape(initialFile)
      .then(setShape)
      .catch(() => setShape(null));
  }, [initialFile]);

  useEffect(() => {
    if (!shape || !estimateKey || !apiUrl) return;
    let cancelled = false;
    (async () => {
      try {
        const token = await getToken();
        const response = await fetch(`${apiUrl}/runs/estimate`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
          body: JSON.stringify({
            row_count: shape.rows,
            col_count: shape.cols,
            provider: llmProvider,
            model: llmModel,
          }),
        });
        if (!response.ok) return;
        const data: CostEstimate = await response.json();
        if (!cancelled) setEstimate({ key: estimateKey, value: data });
      } catch {
        // Non-fatal: the run can be started without an estimate.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [shape, estimateKey, llmProvider, llmModel, apiUrl, getToken]);

  async function handleFileChange(selected: File | null) {
    setFile(selected);
    setSheetOptions(null);
    setSelectedSheet("");
    setError(null);
    setShape(selected ? await csvShape(selected).catch(() => null) : null);

    if (!selected || !/\.(xlsx|xls)$/i.test(selected.name) || !apiUrl) {
      return;
    }

    setCheckingSheets(true);
    try {
      const token = await getToken();
      const formData = new FormData();
      formData.append("file", selected);
      const response = await fetch(`${apiUrl}/runs/excel-sheets`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const { sheets }: { sheets: string[] } = await response.json();
      if (sheets.length > 1) {
        setSheetOptions(sheets);
      }
    } catch {
      // Non-fatal: the picker just won't show. `create_run` still enforces
      // the same rule server-side (400 naming the sheets) if this file
      // really does have more than one sheet, so nothing silently breaks —
      // the user just loses the up-front picker and has to retry.
      toast.error(t("sheetCheckFailed"));
    } finally {
      setCheckingSheets(false);
    }
  }

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
            toast.success(t("toastSuccess"));
          } else if (data.error) {
            toast.error(data.error);
          }
        }
      } catch (err) {
        const message = describeSubmitError(err, t);
        setError(message);
        toast.error(message);
        if (pollTimer.current) clearInterval(pollTimer.current);
        setSubmitting(false);
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!apiUrl) {
      setError(t("missingApiUrl"));
      return;
    }
    if (!file && !manualSpec.trim()) {
      setError(t("missingInput"));
      return;
    }
    if (sheetOptions && sheetOptions.length > 1 && !selectedSheet) {
      setError(t("sheetHint"));
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
      if (sheetOptions && sheetOptions.length > 1 && selectedSheet) {
        formData.append("sheet_name", selectedSheet);
      }
      if (manualSpec.trim()) formData.append("manual_spec", manualSpec.trim());
      formData.append("business_question", businessQuestion);
      // Audit finding H5 / D4: send the UI's active locale so the run's progress
      // messages + Advisor narrative follow what the user is actually seeing,
      // instead of the tenant's stored `users.locale` (which can differ from the
      // UI cookie — an English UI was showing Portuguese progress before this).
      formData.append("locale", locale);
      // Sent under the same field names as `PipelineState["llm_provider_override"]`/
      // `["llm_model_override"]` (`src/ai_etl/core/state.py`). NOTE: as of this
      // change, `POST /runs` (`src/ai_etl/api/routers/runs.py::create_run`) does
      // not yet read these fields for one-off runs — only the saved-pipeline
      // path (`services/pipeline_service.py`) applies an LLM override today. The
      // picker and this wiring are ready; a small backend follow-up is needed
      // to make the selection actually take effect for `/runs`.
      if (llmProvider) formData.append("llm_provider_override", llmProvider);
      if (llmModel) formData.append("llm_model_override", llmModel);

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
      const message = describeSubmitError(err, t);
      setError(message);
      toast.error(message);
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
              <Label htmlFor="file">{t("fileLabel")}</Label>
              {/* Wave 2 item 5, 2026-09-05 mega-audit remediation
                  (docs/work/2026-09-05-mega-audit-remediation-plan.md):
                  `className="hidden"` (display:none) used to remove this
                  input from the tab order entirely — a keyboard-only user
                  tabbing through the form skipped straight over file upload
                  with no way to activate it. `sr-only` (clip-path, not
                  display:none) keeps it visually hidden but focusable; `peer`
                  + `peer-focus-visible:*` on the visual label below gives
                  keyboard focus a visible indicator, since Tab lands on the
                  real input, not the label. The input is placed before the
                  label in markup order so the `peer` sibling selector (which
                  only matches later siblings) can reach it. */}
              <Input
                id="file"
                type="file"
                onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
                disabled={submitting}
                className="sr-only! peer"
              />
              <label
                htmlFor="file"
                className="flex items-center gap-3 border border-dashed rounded-md px-4 py-3 text-sm cursor-pointer hover:bg-accent/50 transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2"
              >
                <UploadCloud className="h-4 w-4 text-muted-foreground shrink-0" />
                <span className="truncate text-muted-foreground">
                  {file ? file.name : t("fileCta")}
                </span>
              </label>
            </div>

            {sheetOptions && sheetOptions.length > 1 && (
              <div className="flex flex-col gap-2">
                <Label htmlFor="sheetName">
                  {t("sheetLabel")}{" "}
                  <span className="text-muted-foreground font-normal">{t("sheetHint")}</span>
                </Label>
                <select
                  id="sheetName"
                  value={selectedSheet}
                  onChange={(e) => setSelectedSheet(e.target.value)}
                  disabled={submitting}
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs outline-none disabled:opacity-50"
                >
                  <option value="">{t("sheetPlaceholder")}</option>
                  {sheetOptions.map((sheet) => (
                    <option key={sheet} value={sheet}>
                      {sheet}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {checkingSheets && (
              <p className="text-muted-foreground text-xs">
                <Loader2 className="h-3 w-3 animate-spin inline mr-1" />
                {t("sheetPlaceholder")}
              </p>
            )}

            <div className="flex flex-col gap-2">
              <Label htmlFor="manualSpec">
                {t("specLabel")}{" "}
                <span className="text-muted-foreground font-normal">
                  {t("specOptionalHint")}
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
              <Label htmlFor="businessQuestion">{t("businessQuestionLabel")}</Label>
              <Textarea
                id="businessQuestion"
                value={businessQuestion}
                onChange={(e) => setBusinessQuestion(e.target.value)}
                disabled={submitting}
                rows={2}
                placeholder={t("businessQuestionPlaceholder")}
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label>
                {t("modelLabel")}{" "}
                <span className="text-muted-foreground font-normal">{t("modelHint")}</span>
              </Label>
              <ModelPicker
                currentProvider={llmProvider}
                currentModel={llmModel}
                onSelect={async (provider, model) => {
                  setLlmProvider(provider);
                  setLlmModel(model);
                }}
                disabled={submitting}
              />
            </div>

            {estimate && shape && estimate.key === estimateKey && (
              <div className="flex flex-col gap-0.5 text-sm" aria-live="polite">
                <span>
                  {t("estimateLabel", { cost: formatUsd(estimate.value.estimated_cost_usd, locale) })}
                </span>
                <span className="text-xs text-muted-foreground">
                  {t(`estimateBasis.${estimate.value.basis}`, {
                    rows: shape.rows.toLocaleString(locale),
                    cols: shape.cols.toLocaleString(locale),
                  })}
                </span>
              </div>
            )}

            <Button
              type="submit"
              disabled={
                submitting || checkingSheets || !!(sheetOptions && sheetOptions.length > 1 && !selectedSheet)
              }
              className="w-full"
            >
              {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
              {submitting ? t("submitLoading") : t("submitIdle")}
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
                  <span className="text-muted-foreground">{t("taskLabel")}</span>
                  <span className="font-mono text-xs">{taskId}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-muted-foreground">{t("statusLabel")}</span>
                  {displayStatus && <StatusBadge status={displayStatus} />}
                </div>
                <AgentProgress
                  meta={status?.meta ?? null}
                  done={!!status?.ready}
                  failed={status?.result?.status === "failed" || !!status?.error}
                />
                {status?.ready && status.result?.status === "completed" && (
                  <p className="text-emerald-400">
                    ✅ {t("completedPrefix")}{" "}
                    <Link href={`/history/${status.result.run_id}`} className="underline">
                      {t("viewRun", { runId: status.result.run_id ?? "" })}
                    </Link>
                  </p>
                )}
                {status?.ready && status.result?.status === "failed" && (
                  <p className="text-destructive">
                    ❌ {splitErrorMessage(status.result.error ?? t("failedNoMessage")).summary}{" "}
                    {status.result.run_id && (
                      <Link href={`/history/${status.result.run_id}`} className="underline">
                        {t("viewFailedRun")}
                      </Link>
                    )}
                  </p>
                )}
                {status?.ready && status.error && (
                  <p className="text-destructive">❌ {splitErrorMessage(status.error).summary}</p>
                )}
              </CardContent>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
