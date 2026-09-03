import type { useTranslations } from "next-intl";

type Translator = ReturnType<typeof useTranslations<"runForm">>;

/**
 * Error-language mapping for `<RunForm>` (`/`, the "technical operator"
 * screen — see `friendly-error.ts`'s own docstring for why that helper is
 * deliberately scoped to `/summary` and not reused here). Unlike the
 * executive screens, this audience benefits from technical detail, so this
 * only normalizes the *shape* of known raw errors into a clear message —
 * it still surfaces an HTTP status or backend `detail` string when useful,
 * it just stops leaking a raw `Error: ...`/English exception string verbatim
 * (e.g. `"Error: Could not parse uploaded file."`) into the UI unstyled.
 *
 * Fixed 2026-09-03 (previously flagged in the 2026-08-26 English-only audit
 * and left as known debt): the returned messages used to be hardcoded
 * Portuguese regardless of the active locale, bypassing `next-intl`
 * entirely — an EN-US visitor would still see Portuguese error text. Now
 * takes the caller's own `useTranslations("runForm")` translator (the
 * `errors.*` keys live under that namespace in `messages/*.json`) instead
 * of returning a literal string, since this is a plain function, not a
 * component/hook, and can't call `useTranslations` itself.
 *
 * Scoped to the error shapes `run-form.tsx` actually produces: a
 * network/fetch failure, a non-OK `/runs` or `/runs/{id}/status` response
 * (body's `detail`, or a bare `HTTP {status}`), and the specific upload
 * "Could not parse uploaded file." detail raised by
 * `src/ai_etl/api/routers/runs.py::create_run`.
 */
export function describeSubmitError(err: unknown, t: Translator): string {
  const raw = err instanceof Error ? err.message : String(err);

  if (/could not parse uploaded file/i.test(raw)) {
    return t("errors.parseFailed");
  }

  if (/failed to fetch|networkerror|load failed|network request failed/i.test(raw)) {
    return t("errors.network");
  }

  const httpMatch = raw.match(/HTTP (\d{3})/);
  if (httpMatch) {
    return t("errors.http", { status: httpMatch[1] });
  }

  const detail = raw.replace(/^Error:\s*/i, "").trim();
  return t("errors.generic", { detail });
}
