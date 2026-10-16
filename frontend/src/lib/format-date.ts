/**
 * Date and time in the interface's language rather than the browser's: a viewer who picked
 * Portuguese sees 12/10/2026, 17:43 even in a browser set to English.
 */
export function formatDateTime(value: string | number | Date, locale: string): string {
  return new Date(value).toLocaleString(locale, { dateStyle: "short", timeStyle: "short" });
}
