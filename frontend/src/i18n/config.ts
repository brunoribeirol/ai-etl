/**
 * Sprint 25 (ADR-036) — frontend locale constants.
 *
 * Deliberately mirrors the backend's `core/locale.py::SUPPORTED_LOCALES`/
 * `DEFAULT_LOCALE` exactly (same two codes, same default) — this is the UI-side
 * half of the same locale, not an independent list that could drift from what
 * `PATCH /tenant/locale` accepts. See ADR-036 §3 for why the frontend UI locale
 * (this cookie) and the backend narrative locale (`users.locale`) are two
 * separate settings kept in sync by one control, not two independent axes.
 */
export const SUPPORTED_LOCALES = ["pt-BR", "en-US"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "pt-BR";

export const LOCALE_LABELS: Record<Locale, string> = {
  "pt-BR": "Português (Brasil)",
  "en-US": "English (US)",
};

export function isSupportedLocale(value: string | undefined | null): value is Locale {
  return SUPPORTED_LOCALES.includes(value as Locale);
}

// Read by the server (`src/i18n/request.ts`) and written by the client toggle
// (`src/components/locale-toggle.tsx` via `src/i18n/actions.ts`'s server action) —
// a long-lived cookie so a visitor's choice survives across sessions, same
// persistence contract as `next-themes`' own `localStorage`-backed toggle.
export const LOCALE_COOKIE_NAME = "AI_ETL_LOCALE";
