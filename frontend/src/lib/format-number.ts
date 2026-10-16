/**
 * Money and decimals in the interface's language rather than in JavaScript's default: a viewer
 * who picked Portuguese sees US$ 0,0089 and 2,01 s, not $0.008889 and 2.01s. The "US$" prefix
 * is kept in both languages because every cost in the product is in US dollars.
 */
export function formatDecimal(value: number, locale: string, fractionDigits: number): string {
  return value.toLocaleString(locale, {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
}

export function formatUsd(value: number, locale: string, fractionDigits = 4): string {
  return `US$ ${formatDecimal(value, locale, fractionDigits)}`;
}
