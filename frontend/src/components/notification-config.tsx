"use client";

import { Bell } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * Sprint 37 (ADR-034) frontend — per-pipeline notification destination
 * override. Consumes `PUT /pipelines/{id}/notification-config` directly (own
 * component-local form state, independent of `pipelines-manager.tsx`'s main
 * edit form), same "separate endpoint, separate control" reasoning
 * `ModelPicker`'s own docstring documents for the LLM override.
 *
 * `notification_target` (the email address / webhook URL) is never returned
 * by any backend endpoint once saved — see
 * `SetPipelineNotificationConfigRequest`'s docstring in
 * `api/routers/pipelines.py`. This form therefore never prefills the target
 * field, same "value never echoed back" convention `secrets-manager.tsx`
 * already established: the field always starts empty, and typing into it
 * only ever *replaces* whatever destination is currently configured.
 *
 * `notification_channel`/`notification_target` are always written together
 * by the backend (clearing the override needs both `None`, same as
 * `SetPipelineLlmConfigRequest`) — there is no partial-update verb for
 * `notification_active` alone. So toggling "active" on an already-configured
 * destination still requires re-entering the target here; `hint` below spells
 * that out rather than leaving it a silent surprise.
 */
export function NotificationConfig({
  currentChannel,
  currentConfigured,
  currentActive,
  allowedChannels,
  onSave,
  onClear,
  disabled,
}: {
  currentChannel: string | null;
  currentConfigured: boolean;
  currentActive: boolean;
  allowedChannels: string[];
  onSave: (channel: string, target: string, active: boolean) => Promise<void>;
  onClear: () => Promise<void>;
  disabled?: boolean;
}) {
  const t = useTranslations("notificationConfig");
  const [channel, setChannel] = useState(currentChannel ?? allowedChannels[0] ?? "email");
  const [target, setTarget] = useState("");
  const [active, setActive] = useState(currentActive);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);

  const isBusy = disabled || saving || clearing;
  const targetIsEmail = channel === "email";

  async function handleSave() {
    if (!target.trim()) return;
    setSaving(true);
    try {
      await onSave(channel, target.trim(), active);
      setTarget("");
    } finally {
      setSaving(false);
    }
  }

  async function handleClear() {
    setClearing(true);
    try {
      await onClear();
      setTarget("");
      setChannel(allowedChannels[0] ?? "email");
      setActive(true);
    } finally {
      setClearing(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground" role="status" aria-live="polite">
        {currentConfigured
          ? t("configuredHint", { channel: t(`channelName.${currentChannel}`) })
          : t("notConfiguredHint")}
      </p>

      <div className="flex flex-col gap-2">
        <Label htmlFor="notificationChannel">{t("channelLabel")}</Label>
        <select
          id="notificationChannel"
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
          disabled={isBusy}
          className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs outline-none disabled:opacity-50"
        >
          {allowedChannels.map((value) => (
            <option key={value} value={value}>
              {t(`channelName.${value}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="notificationTarget">
          {targetIsEmail ? t("targetLabelEmail") : t("targetLabelWebhook")}
        </Label>
        <Input
          id="notificationTarget"
          type={targetIsEmail ? "email" : "url"}
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          disabled={isBusy}
          placeholder={targetIsEmail ? t("targetPlaceholderEmail") : t("targetPlaceholderWebhook")}
          className="font-mono text-sm"
        />
        <p className="text-xs text-muted-foreground">{t("hint")}</p>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={active}
          onChange={(e) => setActive(e.target.checked)}
          disabled={isBusy}
          className="h-4 w-4 rounded border-input"
        />
        {t("activeLabel")}
      </label>

      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={handleSave}
          disabled={isBusy || !target.trim()}
        >
          <Bell className="h-4 w-4" aria-hidden="true" /> {t("saveButton")}
        </Button>
        {currentConfigured && (
          <Button type="button" size="sm" variant="ghost" onClick={handleClear} disabled={isBusy}>
            {t("clearButton")}
          </Button>
        )}
      </div>
    </div>
  );
}
