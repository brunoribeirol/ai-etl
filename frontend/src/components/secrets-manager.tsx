"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Inbox, KeyRound, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuthedFetch } from "@/lib/authed-fetch";
import type { SecretMutationResult } from "@/lib/types";

/**
 * `/segredos` page's data + interaction layer — first frontend consumer of
 * `api/routers/secrets.py` (Sprint 19, ADR-022), previously 100% backend
 * with zero UI. `GET /secrets` returns names only (`list_secret_names`); no
 * endpoint here — nor anywhere in this router — ever returns a decrypted
 * value, so there is nothing to accidentally echo back after the user types
 * it into `value` below. The value field is cleared (state reset) the
 * moment `POST /secrets` succeeds, and is never logged or rendered again.
 *
 * `name` doubles as the identifier `POST` creates *and* rotates (same
 * endpoint either way — the router has no separate "update" verb), so
 * submitting an existing name silently rotates it; the success toast wording
 * stays agnostic ("stored") rather than claiming "created" for what may be a
 * rotation.
 *
 * Delete uses the same inline 2-click confirm `approval-queue.tsx` already
 * established (no new shadcn dependency for a one-off destructive action):
 * first click turns the button into a "confirm" state, a second click
 * within that state actually calls `DELETE /secrets/{name}`.
 *
 * Editor-only end to end: the page wrapping this component re-checks
 * `/config`'s role server-side before rendering it at all (cosmetic), and
 * every fetch below still independently hits a `require_role("editor")`
 * route regardless of what the page shows.
 */
export function SecretsManager() {
  const t = useTranslations("secretsManager");
  const authedFetch = useAuthedFetch();

  const [names, setNames] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [confirmingName, setConfirmingName] = useState<string | null>(null);
  const [deletingName, setDeletingName] = useState<string | null>(null);

  const loadNames = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setNames(await authedFetch<string[]>("/secrets"));
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setLoading(false);
    }
  }, [authedFetch]);

  useEffect(() => {
    // Intentional mount-time fetch, same established pattern as
    // pipelines-manager.tsx::loadPipelines.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadNames();
  }, [loadNames]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    try {
      await authedFetch<SecretMutationResult>("/secrets", {
        method: "POST",
        body: JSON.stringify({ name, value }),
      });
      const wasRotate = names.includes(name);
      toast.success(wasRotate ? t("toastRotated") : t("toastStored"));
      setName("");
      setValue("");
      await loadNames();
    } catch (err) {
      toast.error(String(err instanceof Error ? err.message : err));
    } finally {
      setSubmitting(false);
    }
  }

  function startConfirmDelete(target: string) {
    setConfirmingName(target);
  }

  function cancelConfirmDelete() {
    setConfirmingName(null);
  }

  async function confirmDelete(target: string) {
    setDeletingName(target);
    try {
      await authedFetch<SecretMutationResult>(`/secrets/${encodeURIComponent(target)}`, {
        method: "DELETE",
      });
      toast.success(t("toastDeleted"));
      setConfirmingName(null);
      await loadNames();
    } catch (err) {
      toast.error(String(err instanceof Error ? err.message : err));
    } finally {
      setDeletingName(null);
    }
  }

  return (
    <div className="flex flex-col gap-6 max-w-2xl w-full">
      <Card>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-5">
            <h2 className="text-sm font-medium flex items-center gap-2">
              <KeyRound className="h-4 w-4" aria-hidden="true" />
              {t("formHeading")}
            </h2>

            <div className="flex flex-col gap-2">
              <Label htmlFor="secretName">{t("nameLabel")}</Label>
              <Input
                id="secretName"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={submitting}
                required
                placeholder={t("namePlaceholder")}
                className="font-mono text-sm"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="secretValue">{t("valueLabel")}</Label>
              <Input
                id="secretValue"
                type="password"
                autoComplete="new-password"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                disabled={submitting}
                required
                placeholder={t("valuePlaceholder")}
                className="font-mono text-sm"
              />
              <p className="text-xs text-muted-foreground">{t("valueHint")}</p>
            </div>

            <Button type="submit" disabled={submitting}>
              <Plus className="h-4 w-4" aria-hidden="true" /> {t("submitButton")}
            </Button>
          </form>
        </CardContent>
      </Card>

      <div className="flex flex-col gap-3">
        <h2 className="text-sm font-medium text-muted-foreground">{t("listHeading")}</h2>

        {error && (
          <p className="text-destructive text-sm" role="alert">
            {error}
          </p>
        )}
        {!loading && !error && names.length === 0 && (
          <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground border border-dashed rounded-xl">
            <Inbox className="h-6 w-6" aria-hidden="true" />
            <p className="text-sm">{t("emptyState")}</p>
          </div>
        )}

        {names.length > 0 && (
          <div
            className="flex flex-col gap-2"
            role="status"
            aria-live="polite"
            aria-atomic="false"
          >
            {names.map((secretName) => {
              const isConfirming = confirmingName === secretName;
              const isDeleting = deletingName === secretName;
              return (
                <Card key={secretName}>
                  <CardContent className="flex justify-between items-center gap-3 text-sm py-3">
                    <span className="font-mono">{secretName}</span>
                    <div className="flex gap-2">
                      {isConfirming && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={isDeleting}
                          onClick={cancelConfirmDelete}
                        >
                          {t("cancel")}
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={isDeleting}
                        onClick={() =>
                          isConfirming ? confirmDelete(secretName) : startConfirmDelete(secretName)
                        }
                      >
                        <Trash2 className="h-4 w-4" aria-hidden="true" />
                        {isConfirming ? t("confirmDelete") : t("delete")}
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
