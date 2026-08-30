"use client";

import { useAuth } from "@clerk/nextjs";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { Button } from "@/components/ui/button";
import { setLocaleCookie } from "@/i18n/actions";
import type { Locale } from "@/i18n/config";

/**
 * Sprint 25 (ADR-036) — EN/PT-BR toggle, accessible from every screen
 * (mounted in both `(app)/layout.tsx` and `(marketing)/layout.tsx` headers,
 * next to `<ThemeToggle>`). Two-state button (not a dropdown) — same
 * "minimal chrome, no menu" shape as `<ThemeToggle>`, since there are only
 * two supported locales (`core/locale.py::SUPPORTED_LOCALES`, mirrored in
 * `src/i18n/config.ts`).
 *
 * `syncBackend`: when `true` (only inside the authenticated `(app)` shell —
 * the marketing page has no signed-in tenant to configure), the same click
 * that flips the UI cookie also calls `PATCH /tenant/locale`, so one control
 * changes both "what language this browser renders chrome in" and "what
 * language the Advisor/Analyst/Science narrative is generated in on the next
 * run" (ADR-036 §3) — a visitor never has to find two separate settings for
 * what reads as one preference. Best-effort: a failed backend sync doesn't
 * block the (always-successful, cookie-only) UI toggle — the tenant can
 * retry from a dedicated settings control later; this toggle's job is
 * primarily the UI, and it degrades gracefully if the API call fails.
 */
export function LocaleToggle({ syncBackend = false }: { syncBackend?: boolean }) {
  const locale = useLocale() as Locale;
  const t = useTranslations("localeToggle");
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const { getToken } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;

  const nextLocale: Locale = locale === "pt-BR" ? "en-US" : "pt-BR";

  function handleClick() {
    // Real bug found 2026-08-30: the backend sync used to be awaited inside
    // this same transition, ahead of router.refresh() — the button stayed
    // disabled for a full round-trip to the Railway API on every click, on
    // top of the cookie-set round-trip, contradicting this component's own
    // "best-effort, never blocks the UI toggle" docstring above. The PATCH
    // now fires independently of the transition — the UI updates as soon as
    // the cookie is set, exactly the intended behavior.
    if (syncBackend && apiUrl) {
      getToken()
        .then((token) =>
          fetch(`${apiUrl}/tenant/locale`, {
            method: "PATCH",
            headers: {
              Authorization: `Bearer ${token}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ locale: nextLocale }),
          }),
        )
        .catch(() => {
          // Best-effort — see docstring above.
        });
    }
    startTransition(async () => {
      await setLocaleCookie(nextLocale);
      router.refresh();
    });
  }

  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={handleClick}
      disabled={isPending}
      aria-label={t("switchTo", { locale: nextLocale })}
    >
      <span className="text-xs font-semibold" aria-hidden>
        {locale === "pt-BR" ? "PT" : "EN"}
      </span>
      <span className="sr-only">{t("switchTo", { locale: nextLocale })}</span>
    </Button>
  );
}
