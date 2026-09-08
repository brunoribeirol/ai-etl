"use client";

import { Check, Loader2, Wifi, WifiOff } from "lucide-react";
import { useTranslations } from "next-intl";
import { type MouseEvent, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useAuthedFetch } from "@/lib/authed-fetch";
import { cn } from "@/lib/utils";
import { MODEL_REFERENCE_DATA } from "@/lib/model-reference-data";

type TestResult = { ok: boolean; latencyMs: number; error: string | null };

/**
 * Sprint 30 (ADR-031) frontend — per-pipeline LLM model picker. Consumes
 * `PUT /pipelines/{id}/llm-config` directly (own component-local loading
 * state, independent of `pipelines-manager.tsx`'s main edit form — this
 * writes immediately on click, same "separate endpoint, separate control"
 * reasoning `SetPipelineLlmConfigRequest`'s own docstring documents for why
 * it isn't folded into `UpdatePipelineRequest`).
 *
 * Only the 4 cloud models in `MODEL_REFERENCE_DATA` are shown — no local
 * (Ollama) reference cards. Product decision (2026-08-23): local models will
 * never be client-selectable and have no place in this UI even as a
 * non-clickable reference (see that module's own docstring for the full
 * rationale).
 *
 * Gap-closing fix (2026-09-08): `POST /llm/test-connectivity` (ADR-031,
 * `api/routers/llm.py`) has existed since Sprint 30 — a real, working
 * endpoint that makes one live LLM call and reports success/failure — but
 * no frontend ever called it. A tenant picking a model had no way to find
 * out it didn't work (bad/missing credential, wrong model id) except
 * running a real pipeline and watching it fail mid-run. The per-card "Test
 * connection" button below is the missing wiring, nothing new on the
 * backend.
 */
export function ModelPicker({
  currentProvider,
  currentModel,
  onSelect,
  disabled,
}: {
  currentProvider: string | null;
  currentModel: string | null;
  onSelect: (provider: string, model: string) => Promise<void>;
  disabled?: boolean;
}) {
  const t = useTranslations("modelPicker");
  const authedFetch = useAuthedFetch();
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [testingKey, setTestingKey] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  async function handleClick(provider: string, model: string) {
    if (disabled) return;
    const key = `${provider}:${model}`;
    setPendingKey(key);
    try {
      await onSelect(provider, model);
    } finally {
      setPendingKey(null);
    }
  }

  async function handleTest(e: MouseEvent, provider: string, model: string) {
    e.stopPropagation(); // don't also trigger the card's own onClick (select)
    const key = `${provider}:${model}`;
    setTestingKey(key);
    try {
      const result = await authedFetch<{
        ok: boolean;
        latency_ms: number;
        error: string | null;
      }>("/llm/test-connectivity", {
        method: "POST",
        body: JSON.stringify({ provider, model }),
      });
      setTestResults((prev) => ({
        ...prev,
        [key]: { ok: result.ok, latencyMs: result.latency_ms, error: result.error },
      }));
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [key]: { ok: false, latencyMs: 0, error: err instanceof Error ? err.message : String(err) },
      }));
    } finally {
      setTestingKey(null);
    }
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {MODEL_REFERENCE_DATA.map((entry) => {
        const key = `${entry.provider}:${entry.model}`;
        const isActive = currentProvider === entry.provider && currentModel === entry.model;
        const isPending = pendingKey === key;
        const isTesting = testingKey === key;
        const testResult = testResults[key];
        return (
          <Card
            key={key}
            role="button"
            tabIndex={0}
            onClick={() => handleClick(entry.provider, entry.model)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                handleClick(entry.provider, entry.model);
              }
            }}
            className={cn(
              "cursor-pointer transition-colors hover:ring-primary/50",
              isActive && "ring-2 ring-primary",
              (disabled || isPending) && "opacity-60 pointer-events-none",
            )}
          >
            <CardContent className="flex flex-col gap-2 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{entry.displayName}</span>
                {isActive && <Check className="h-4 w-4 text-primary shrink-0" />}
              </div>
              <Badge variant="outline" className="w-fit capitalize">
                {entry.provider}
              </Badge>
              <span className="text-xs text-muted-foreground font-mono">
                {t("priceLine", {
                  input: entry.inputPricePerMillionUsd,
                  output: entry.outputPricePerMillionUsd,
                })}
              </span>
              <span className="text-xs text-emerald-500">{t("reliabilityVerified")}</span>
              <p className="text-xs text-muted-foreground">{entry.reliabilityNote}</p>

              <div className="flex items-center gap-2 pt-1">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={isTesting}
                  onClick={(e) => handleTest(e, entry.provider, entry.model)}
                >
                  {isTesting ? (
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                  ) : (
                    <Wifi className="h-3 w-3" aria-hidden="true" />
                  )}
                  {isTesting ? t("testing") : t("testButton")}
                </Button>
                {testResult && !isTesting && (
                  <span
                    className={cn(
                      "flex items-center gap-1 text-xs",
                      testResult.ok ? "text-emerald-500" : "text-destructive",
                    )}
                    title={testResult.error ?? undefined}
                  >
                    {testResult.ok ? (
                      <Wifi className="h-3 w-3" aria-hidden="true" />
                    ) : (
                      <WifiOff className="h-3 w-3" aria-hidden="true" />
                    )}
                    {testResult.ok
                      ? t("testSuccess", { latency: Math.round(testResult.latencyMs) })
                      : t("testFailure")}
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
