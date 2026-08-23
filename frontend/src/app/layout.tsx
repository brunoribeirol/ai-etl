import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { NextIntlClientProvider } from "next-intl";
import { getLocale } from "next-intl/server";
import { ClerkProvider } from "@clerk/nextjs";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AI-ETL — Pipelines de dados que se explicam",
  description:
    "Descreva o pipeline em linguagem natural. Agentes de IA extraem, limpam e carregam seus dados heterogêneos gerando código Python auditável — não uma resposta de chat que some quando você fecha a aba.",
};

/**
 * Root layout, deliberately thin (landing-page addition, no sprint number —
 * see docs/CURRENT_STATE.md). Previously this file *was* the whole app shell
 * (header, nav, the authenticated "Como funciona" panel) — that only makes
 * sense once a visitor is inside the product. Now `/` is a public marketing
 * page with its own layout (`(marketing)/layout.tsx`) and everything
 * previously at `/` lives at `/app` under `(app)/layout.tsx`, which owns the
 * header/nav/auth chrome this file used to render directly. This file keeps
 * only what every route — marketing and app alike — genuinely needs:
 * `ClerkProvider` (so `(marketing)`'s sign-in button and `(app)`'s
 * `auth.protect()` share one session), fonts, and (Sprint 38) the theme
 * provider driving the light/dark toggle.
 *
 * `className` no longer hardcodes `dark` (dark-only since Sprint 7) —
 * `ThemeProvider` (`next-themes`, `attribute="class"`) now sets/removes that
 * class on `<html>` itself, reading the visitor's stored/system preference.
 * `suppressHydrationWarning` is required by `next-themes` for exactly this
 * element: the class it applies client-side after reading `localStorage`
 * legitimately differs from the class-less server-rendered markup.
 *
 * Sprint 25 (ADR-036) — `NextIntlClientProvider` makes the message catalog
 * `src/i18n/request.ts` resolved for this request (from the locale cookie,
 * not the URL — see `next.config.ts`) available to every Client Component
 * via `useTranslations`/`useLocale`, same "resolve once at the root, thread
 * down via context" shape as `ThemeProvider` above it. `<html lang>` now
 * reflects the resolved locale instead of being hardcoded `"pt-BR"`.
 */
export default async function RootLayout({ children }: LayoutProps<"/">) {
  const locale = await getLocale();

  return (
    <ClerkProvider>
      <html
        lang={locale}
        className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
        suppressHydrationWarning
      >
        <body className="min-h-full flex flex-col bg-background text-foreground">
          <NextIntlClientProvider>
            <ThemeProvider>
              {children}
              <Toaster richColors position="top-right" />
            </ThemeProvider>
          </NextIntlClientProvider>
        </body>
      </html>
    </ClerkProvider>
  );
}
