/**
 * Sprint 38 — error-language review for the executive screens (`/resumo`,
 * `/resumo/[id]`). Before this, both pages and `<ExecutiveSummary>` rendered
 * `String(err)` straight from `apiFetch`/`fetch` failures directly to a
 * non-technical audience — raw strings like `"Error: HTTP 404"` or
 * `"Error: NEXT_PUBLIC_API_URL is not configured."`. This maps the error
 * shapes those call sites actually throw (see `lib/api.ts` and
 * `executive-summary.tsx`) to plain-language Portuguese; anything
 * unrecognized falls back to one honest, jargon-free message instead of
 * leaking an HTTP status code or an env var name.
 *
 * Deliberately narrow in scope — the technical screens (`/`, `/pipelines`,
 * `/historico`) keep showing the raw `String(err)` they already did; that
 * audience is the "operador técnico" persona (see the landing page's own
 * copy) who benefits from the precise detail.
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
