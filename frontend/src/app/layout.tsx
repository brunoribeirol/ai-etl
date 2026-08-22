import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ClerkProvider } from "@clerk/nextjs";
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
 * `auth.protect()` share one session), fonts, and the dark theme class.
 */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <ClerkProvider>
      <html
        lang="pt-BR"
        className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      >
        <body className="min-h-full flex flex-col bg-background text-foreground">
          {children}
          <Toaster richColors position="top-right" />
        </body>
      </html>
    </ClerkProvider>
  );
}
