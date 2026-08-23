"use client";

import { Check } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { MODEL_REFERENCE_DATA } from "@/lib/model-reference-data";

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
  const [pendingKey, setPendingKey] = useState<string | null>(null);

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

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {MODEL_REFERENCE_DATA.map((entry) => {
        const key = `${entry.provider}:${entry.model}`;
        const isActive = currentProvider === entry.provider && currentModel === entry.model;
        const isPending = pendingKey === key;
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
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
