/**
 * Error-language mapping for `<RunForm>` (`/`, the "technical operator"
 * screen — see `friendly-error.ts`'s own docstring for why that helper is
 * deliberately scoped to `/summary` and not reused here). Unlike the
 * executive screens, this audience benefits from technical detail, so this
 * only normalizes the *shape* of known raw errors into clear Portuguese —
 * it still surfaces an HTTP status or backend `detail` string when useful,
 * it just stops leaking a raw `Error: ...`/English exception string verbatim
 * (e.g. `"Error: Could not parse uploaded file."`) into an otherwise
 * Portuguese-language UI.
 *
 * NOTE (2026-08-26 English-only audit): the returned message strings below
 * are deliberately hardcoded Portuguese regardless of the active locale —
 * this predates the current audit, is out of this task's declared file list
 * (renames/route/i18n-key scope only), and needs its own follow-up to route
 * through `next-intl` if/when this screen's copy should also honor the
 * locale toggle. Left untouched here; flagged for the repo owner to confirm.
 *
 * Scoped to the error shapes `run-form.tsx` actually produces: a
 * network/fetch failure, a non-OK `/runs` or `/runs/{id}/status` response
 * (body's `detail`, or a bare `HTTP {status}`), and the specific upload
 * "Could not parse uploaded file." detail raised by
 * `src/ai_etl/api/routers/runs.py::create_run`.
 */
export function describeSubmitError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);

  if (/could not parse uploaded file/i.test(raw)) {
    return "Não foi possível processar o arquivo enviado. Verifique o formato (CSV, Excel, JSON, PDF ou DOCX) e tente novamente.";
  }

  if (/failed to fetch|networkerror|load failed|network request failed/i.test(raw)) {
    return "Falha de conexão com o servidor. Verifique sua internet e tente novamente.";
  }

  const httpMatch = raw.match(/HTTP (\d{3})/);
  if (httpMatch) {
    return `Erro ao comunicar com o servidor (HTTP ${httpMatch[1]}). Tente novamente em instantes.`;
  }

  const detail = raw.replace(/^Error:\s*/i, "").trim();
  return `Ocorreu um erro ao processar a solicitação. Detalhe técnico: ${detail}`;
}
