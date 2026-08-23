"use server";

import { cookies } from "next/headers";
import { LOCALE_COOKIE_NAME, type Locale } from "./config";

/**
 * Sprint 25 (ADR-036) — persists the visitor's UI locale choice. A Server
 * Action (not a `/api/locale` route handler) so `src/components/locale-toggle.tsx`
 * can call it directly and rely on Next.js re-rendering every Server Component
 * on the page with the new cookie value already in place, same "no client-side
 * reload needed" ergonomics `next-themes`' `setTheme` gets from `localStorage` +
 * a `useEffect`.
 *
 * One year, `Path=/`: a long-lived, whole-app preference — same lifetime class
 * as `next-themes`' own persisted choice, not a per-session cookie.
 */
export async function setLocaleCookie(locale: Locale): Promise<void> {
  const cookieStore = await cookies();
  cookieStore.set(LOCALE_COOKIE_NAME, locale, {
    path: "/",
    maxAge: 60 * 60 * 24 * 365,
    sameSite: "lax",
  });
}
