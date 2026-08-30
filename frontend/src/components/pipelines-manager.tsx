"use client";

import { useAuth } from "@clerk/nextjs";
import { FileText, LineChart, PauseCircle, PlayCircle, Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { StatusBadge } from "@/components/status-badge";
import { ModelPicker } from "@/components/model-picker";
import { SCHEDULABLE_SOURCE_TYPES, type QualityRule, type SavedPipeline } from "@/lib/types";

/**
 * Sprint 13 (ADR-016) — minimal CRUD UI for saved (recurring) pipelines,
 * distinct from the "Run" page's one-off `POST /runs`. Client
 * Component (needs `useAuth().getToken()` fresh per request, same pattern
 * `run-form.tsx` already established) — no server-side prefetch, since
 * every action here (create/pause/resume/edit) needs a token anyway.
 *
 * Deliberately minimal per the sprint's own scope ("doesn't need to be
 * pretty, needs to work"): a plain list + inline edit, no separate route per
 * pipeline, native `<select>` for source_type rather than pulling in a new
 * shadcn Select component for one dropdown.
 */
export function PipelinesManager() {
  const t = useTranslations("pipelinesManager");
  const { getToken } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;

  const [pipelines, setPipelines] = useState<SavedPipeline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [sourceType, setSourceType] = useState<string>(SCHEDULABLE_SOURCE_TYPES[0]);
  const [spec, setSpec] = useState("");
  const [businessQuestion, setBusinessQuestion] = useState("");
  const [cronSchedule, setCronSchedule] = useState("0 3 * * *");
  // Sprint 16 (ADR-023) — operator-defined quality rules, edited as raw JSON: a
  // plain textarea rather than a per-field rule builder, matching this manager's
  // own Sprint 13 precedent ("doesn't need to be pretty, needs to work").
  const [qualityRulesText, setQualityRulesText] = useState("[]");
  const [submitting, setSubmitting] = useState(false);
  // Sprint 30 (ADR-031) frontend — the pipeline currently being edited's LLM
  // provider/model override, kept separate from the fields above since it
  // writes via its own endpoint (`PUT /pipelines/{id}/llm-config`), not the
  // main PATCH `handleSubmit` below.
  const [llmProvider, setLlmProvider] = useState<string | null>(null);
  const [llmModel, setLlmModel] = useState<string | null>(null);

  const authedFetch = useCallback(
    async (path: string, init?: RequestInit) => {
      if (!apiUrl) throw new Error(t("missingApiUrl"));
      const token = await getToken();
      const response = await fetch(`${apiUrl}${path}`, {
        ...init,
        headers: {
          Authorization: `Bearer ${token}`,
          ...(init?.body ? { "Content-Type": "application/json" } : {}),
          ...init?.headers,
        },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail ?? `HTTP ${response.status}`);
      }
      return response.json();
    },
    [apiUrl, getToken, t],
  );

  const loadPipelines = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await authedFetch("/pipelines");
      setPipelines(data as SavedPipeline[]);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [authedFetch]);

  useEffect(() => {
    // Intentional mount-time fetch; `loadPipelines` synchronously resets
    // loading/error before its `await`, same established pattern as
    // executive-summary.tsx/pipeline-history.tsx. No data-fetching library
    // (SWR/React Query) in this codebase to delegate this to.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadPipelines();
  }, [loadPipelines]);

  function resetForm() {
    setEditingId(null);
    setName("");
    setSourceType(SCHEDULABLE_SOURCE_TYPES[0]);
    setSpec("");
    setBusinessQuestion("");
    setCronSchedule("0 3 * * *");
    setQualityRulesText("[]");
    setLlmProvider(null);
    setLlmModel(null);
  }

  function startEdit(pipeline: SavedPipeline) {
    setEditingId(pipeline.id);
    setName(pipeline.name);
    setSourceType(pipeline.source_type);
    setSpec(pipeline.spec);
    setBusinessQuestion(pipeline.business_question);
    setCronSchedule(pipeline.cron_schedule);
    setQualityRulesText(JSON.stringify(pipeline.quality_rules ?? [], null, 2));
    setLlmProvider(pipeline.llm_provider);
    setLlmModel(pipeline.llm_model);
  }

  /** Sprint 30 (ADR-031) frontend — writes a model-picker click immediately via
   * `PUT /pipelines/{id}/llm-config`, independent of the main form's `Save`
   * button (see `<ModelPicker>`'s own docstring for why). */
  async function handleModelSelect(provider: string, model: string) {
    if (!editingId) return;
    try {
      await authedFetch(`/pipelines/${editingId}/llm-config`, {
        method: "PUT",
        body: JSON.stringify({ llm_provider: provider, llm_model: model }),
      });
      setLlmProvider(provider);
      setLlmModel(model);
      toast.success(t("toastModelUpdated"));
      await loadPipelines();
    } catch (err) {
      toast.error(String(err instanceof Error ? err.message : err));
    }
  }

  /** Sprint 16 (ADR-023) — parses the raw JSON textarea into `QualityRule[]`,
   * throwing a Portuguese, actionable message on invalid JSON or a non-array
   * shape (caught by `handleSubmit`, surfaced via `toast.error`, submit aborted
   * *before* any request reaches the API — same "fail loud before the network
   * call" posture `validate_cron_schedule` already has server-side). */
  function parseQualityRules(): QualityRule[] {
    let parsed: unknown;
    try {
      parsed = JSON.parse(qualityRulesText);
    } catch {
      throw new Error(t("invalidJson"));
    }
    if (!Array.isArray(parsed)) {
      throw new Error(t("invalidJsonShape"));
    }
    return parsed as QualityRule[];
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    try {
      const qualityRules = parseQualityRules();
      if (editingId) {
        await authedFetch(`/pipelines/${editingId}`, {
          method: "PATCH",
          body: JSON.stringify({
            name,
            source_type: sourceType,
            spec,
            business_question: businessQuestion,
            cron_schedule: cronSchedule,
            quality_rules: qualityRules,
          }),
        });
        toast.success(t("toastUpdated"));
      } else {
        await authedFetch("/pipelines", {
          method: "POST",
          body: JSON.stringify({
            name,
            source_type: sourceType,
            spec,
            business_question: businessQuestion,
            cron_schedule: cronSchedule,
            quality_rules: qualityRules,
          }),
        });
        toast.success(t("toastCreated"));
      }
      resetForm();
      await loadPipelines();
    } catch (err) {
      toast.error(String(err instanceof Error ? err.message : err));
    } finally {
      setSubmitting(false);
    }
  }

  async function toggleActive(pipeline: SavedPipeline) {
    try {
      await authedFetch(`/pipelines/${pipeline.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !pipeline.is_active }),
      });
      toast.success(pipeline.is_active ? t("toastPaused") : t("toastResumed"));
      await loadPipelines();
    } catch (err) {
      toast.error(String(err));
    }
  }

  return (
    <div className="flex flex-col gap-6 max-w-2xl w-full">
      <Card>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-5">
            <h2 className="text-sm font-medium">
              {editingId ? t("editHeading") : t("newHeading")}
            </h2>

            <div className="flex flex-col gap-2">
              <Label htmlFor="name">{t("nameLabel")}</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={submitting}
                required
                placeholder={t("namePlaceholder")}
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="sourceType">
                {t("sourceTypeLabel")}{" "}
                <span className="text-muted-foreground font-normal">{t("sourceTypeHint")}</span>
              </Label>
              <select
                id="sourceType"
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value)}
                disabled={submitting}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs outline-none disabled:opacity-50"
              >
                {SCHEDULABLE_SOURCE_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="spec">{t("specLabel")}</Label>
              <Textarea
                id="spec"
                value={spec}
                onChange={(e) => setSpec(e.target.value)}
                disabled={submitting}
                rows={3}
                required
                placeholder="Read schema.orders from postgres, filter status=active..."
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
              <Label htmlFor="cronSchedule">
                {t("cronLabel")}{" "}
                <span className="text-muted-foreground font-normal">{t("cronHint")}</span>
              </Label>
              <Input
                id="cronSchedule"
                value={cronSchedule}
                onChange={(e) => setCronSchedule(e.target.value)}
                disabled={submitting}
                required
                className="font-mono text-sm"
                placeholder="0 3 * * *"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="qualityRules">
                {t("qualityRulesLabel")}{" "}
                <span className="text-muted-foreground font-normal font-mono">
                  {'JSON, ex: [{"column": "amount", "operator": "gte", "value": 0}]'}
                </span>
              </Label>
              <Textarea
                id="qualityRules"
                value={qualityRulesText}
                onChange={(e) => setQualityRulesText(e.target.value)}
                disabled={submitting}
                rows={4}
                className="font-mono text-xs"
                placeholder='[{"column": "customer_id", "operator": "not_null"}]'
              />
              <p className="text-xs text-muted-foreground">{t("qualityRulesHelp")}</p>
            </div>

            {/* Sprint 30 (ADR-031) frontend — only shown while editing an
                already-saved pipeline: the model picker needs a real
                pipeline id to call `PUT /pipelines/{id}/llm-config` against,
                which doesn't exist yet for a pipeline still being created. */}
            {editingId && (
              <div className="flex flex-col gap-2">
                <Label>
                  {t("modelLabel")}{" "}
                  <span className="text-muted-foreground font-normal">{t("modelHint")}</span>
                </Label>
                <ModelPicker
                  currentProvider={llmProvider}
                  currentModel={llmModel}
                  onSelect={handleModelSelect}
                  disabled={submitting}
                />
              </div>
            )}

            <div className="flex gap-2">
              <Button type="submit" disabled={submitting} className="flex-1">
                {editingId ? t("saveChanges") : (
                  <>
                    <Plus className="h-4 w-4" /> {t("schedulePipeline")}
                  </>
                )}
              </Button>
              {editingId && (
                <Button type="button" variant="outline" onClick={resetForm} disabled={submitting}>
                  {t("cancel")}
                </Button>
              )}
            </div>
          </form>
        </CardContent>
      </Card>

      <div className="flex flex-col gap-3">
        <h2 className="text-sm font-medium text-muted-foreground">{t("savedPipelinesHeading")}</h2>

        {error && (
          <p className="text-destructive text-sm" role="alert">
            {error}
          </p>
        )}
        {!loading && !error && pipelines.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("emptyState")}</p>
        )}

        {pipelines.map((pipeline) => (
          <Card key={pipeline.id}>
            <CardContent className="flex flex-col gap-2 text-sm">
              <div className="flex justify-between items-start gap-3">
                <div className="flex flex-col gap-1">
                  <span className="font-medium">{pipeline.name}</span>
                  <span className="text-xs text-muted-foreground font-mono">
                    {pipeline.source_type} · {pipeline.cron_schedule}
                  </span>
                </div>
                <StatusBadge status={pipeline.is_active ? "running" : "pending"} />
              </div>
              <p className="text-xs text-muted-foreground truncate">{pipeline.spec}</p>
              {pipeline.quality_rules?.length > 0 && (
                <span className="text-xs text-muted-foreground">
                  {pipeline.quality_rules.length}{" "}
                  {pipeline.quality_rules.length === 1
                    ? t("customQualityRuleOne")
                    : t("customQualityRuleOther")}
                </span>
              )}
              <div className="flex justify-between items-center text-xs text-muted-foreground">
                <span>{t("nextRun", { when: new Date(pipeline.next_run_at).toLocaleString() })}</span>
                {pipeline.last_run_at && (
                  <span>{t("lastRun", { when: new Date(pipeline.last_run_at).toLocaleString() })}</span>
                )}
              </div>
              {/* Sprint 15 (ADR-020) — minimal health badge, no dedicated page
                  (that's Sprint 18, UI executiva). Only shown once a pipeline
                  has actually failed at least once — a healthy pipeline shows
                  nothing extra here. */}
              {pipeline.consecutive_failures > 0 && (
                <div className="text-xs text-red-400" role="alert">
                  {pipeline.consecutive_failures}{" "}
                  {pipeline.consecutive_failures === 1
                    ? t("consecutiveFailureOne")
                    : t("consecutiveFailureOther")}
                  {pipeline.last_error && ` — ${pipeline.last_error}`}
                </div>
              )}
              <div className="flex gap-2 pt-1">
                <Button
                  size="sm"
                  variant="outline"
                  render={
                    // Sprint 18 (ADR-024) — plain-language view for a
                    // non-technical stakeholder, separate from "History"
                    // (run-by-run technical detail, Pipeline/Code tabs).
                    <Link href={`/summary/${pipeline.id}`}>
                      <FileText className="h-4 w-4" /> {t("summary")}
                    </Link>
                  }
                />
                <Button
                  size="sm"
                  variant="outline"
                  render={
                    <Link href={`/pipelines/${pipeline.id}/history`}>
                      <LineChart className="h-4 w-4" /> {t("history")}
                    </Link>
                  }
                />
                <Button size="sm" variant="outline" onClick={() => startEdit(pipeline)}>
                  {t("edit")}
                </Button>
                <Button size="sm" variant="outline" onClick={() => toggleActive(pipeline)}>
                  {pipeline.is_active ? (
                    <>
                      <PauseCircle className="h-4 w-4" /> {t("pause")}
                    </>
                  ) : (
                    <>
                      <PlayCircle className="h-4 w-4" /> {t("resume")}
                    </>
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
