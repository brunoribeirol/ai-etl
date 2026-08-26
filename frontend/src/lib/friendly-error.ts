/**
 * Sprint 38 — error-language review for the executive screens (`/summary`,
 * `/summary/[id]`). Before this, both pages and `<ExecutiveSummary>` rendered
 * `String(err)` straight from `apiFetch`/`fetch` failures directly to a
 * non-technical audience — raw strings like `"Error: HTTP 404"` or
 * `"Error: NEXT_PUBLIC_API_URL is not configured."`. This maps the error
 * shapes those call sites actually throw (see `lib/api.ts` and
 * `executive-summary.tsx`) to plain-language Portuguese; anything
 * unrecognized falls back to one honest, jargon-free message instead of
 * leaking an HTTP status code or an env var name.
 *
 * Deliberately narrow in scope — the technical screens (`/`, `/pipelines`,
 * `/history`) keep showing the raw `String(err)` they already did; that
 * audience is the "technical operator" persona (see the landing page's own
 * copy) who benefits from the precise detail.
 *
 * NOTE (2026-08-26 English-only audit): the returned message strings below
 * are deliberately hardcoded Portuguese regardless of the active locale —
 * this predates the current audit, is out of this task's declared file list
 * (renames/route/i18n-key scope only), and needs its own follow-up to route
 * through `next-intl` if/when this screen's copy should also honor the
 * locale toggle. Left untouched here; flagged for the repo owner to confirm.
 */
export function friendlyExecutiveError(raw: string): string {
  if (/HTTP 404/i.test(raw)) {
    return "Não encontramos esse pipeline. Ele pode ter sido removido ou o link estar incorreto.";
  }
  if (/HTTP 401|HTTP 403/i.test(raw)) {
    return "Você não tem permissão para ver este conteúdo. Fale com quem administra a conta.";
  }
  if (/NEXT_PUBLIC_API_URL/i.test(raw)) {
    return "O sistema está com um problema de configuração no momento. Avise o time técnico.";
  }
  return "Não conseguimos carregar essas informações agora. Tente novamente em alguns instantes.";
}
