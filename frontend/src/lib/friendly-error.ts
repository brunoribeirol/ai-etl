/**
 * Sprint 38 — error-language review for the executive screens (`/summary`,
 * `/summary/[id]`). Before this, both pages and `<ExecutiveSummary>` rendered
 * `String(err)` straight from `apiFetch`/`fetch` failures directly to a
 * non-technical audience — raw strings like `"Error: HTTP 404"` or
 * `"Error: NEXT_PUBLIC_API_URL is not configured."`. This maps the error
 * shapes those call sites actually throw (see `lib/api.ts` and
 * `executive-summary.tsx`) to plain-language text; anything unrecognized
 * falls back to one honest, jargon-free message instead of leaking an HTTP
 * status code or an env var name.
 *
 * Deliberately narrow in scope — the technical screens (`/`, `/pipelines`,
 * `/history`) keep showing the raw `String(err)` they already did; that
 * audience is the "technical operator" persona (see the landing page's own
 * copy) who benefits from the precise detail.
 *
 * Fixed 2026-09-03 (previously flagged in the 2026-08-26 English-only audit
 * and left as known debt, same bug as `form-error.ts` had): the returned
 * messages used to be hardcoded Portuguese regardless of the active locale.
 * Now takes a translator scoped to the shared `executiveErrors` namespace
 * (`messages/*.json`) — one copy of these 4 strings per language, reused by
 * all three call sites (`summary/page.tsx`, `summary/[id]/page.tsx` via
 * `next-intl/server`'s `getTranslations`, and `executive-summary.tsx` via
 * the client `useTranslations`) rather than one per call site's own
 * namespace, so the two don't drift out of sync. Typed as a plain
 * `(key: string) => string` rather than `ReturnType<typeof useTranslations>`
 * specifically so both the server and client translator shapes satisfy it
 * without a cast.
 */
type Translator = (key: string) => string;

export function friendlyExecutiveError(raw: string, t: Translator): string {
  if (/HTTP 404/i.test(raw)) {
    return t("notFound");
  }
  if (/HTTP 401|HTTP 403/i.test(raw)) {
    return t("forbidden");
  }
  if (/NEXT_PUBLIC_API_URL/i.test(raw)) {
    return t("misconfigured");
  }
  return t("generic");
}
