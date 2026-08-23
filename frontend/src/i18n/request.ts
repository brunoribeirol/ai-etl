import { cookies } from "next/headers";
import { getRequestConfig } from "next-intl/server";
import { DEFAULT_LOCALE, LOCALE_COOKIE_NAME, isSupportedLocale } from "./config";

/**
 * Sprint 25 (ADR-036) — resolves the request's locale from a cookie (not the URL,
 * see `next.config.ts`'s comment) and loads that locale's message catalog. No
 * `Accept-Language` negotiation on first visit: an unset cookie defaults to
 * `DEFAULT_LOCALE` ("pt-BR"), matching this app's existing all-Portuguese
 * behavior exactly — a visitor who has never touched the toggle sees the same
 * experience as before this sprint, zero behavior change until they opt in.
 */
export default getRequestConfig(async () => {
  const cookieStore = await cookies();
  const cookieLocale = cookieStore.get(LOCALE_COOKIE_NAME)?.value;
  const locale = isSupportedLocale(cookieLocale) ? cookieLocale : DEFAULT_LOCALE;

  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
