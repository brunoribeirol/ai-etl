import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ClerkProvider } from "@clerk/nextjs";
import Link from "next/link";
import { AgentsInfo } from "@/components/agents-info";
import { AuthHeader } from "@/components/auth-header";
import { Toaster } from "@/components/ui/sonner";
import { apiFetch } from "@/lib/api";
import type { ApiConfig } from "@/lib/types";
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
  title: "AI-ETL",
  description: "Agentic ETL — upload messy data, ask a business question.",
};

/**
 * Sprint 7 redesign (ADR-011 surface, no route/auth contract change) — dark
 * mode by default (shadcn's recommendation for dashboard/AI product surfaces:
 * see vercel:shadcn skill, "Default aesthetic for product UI"), shadcn Card
 * header nav instead of plain links, Toaster mounted once at the root for
 * error/success notifications the old plain-text `<p>` errors didn't have.
 */
export default async function RootLayout({ children }: LayoutProps<"/">) {
  // Best-effort: the model badge / "Como funciona" panel is informational,
  // not load-bearing — a config-fetch failure shouldn't break every page's
  // layout the way a failed page-level fetch would.
  let modelName: string | null = null;
  try {
    modelName = (await apiFetch<ApiConfig>("/config")).model_name;
  } catch {
    modelName = null;
  }

  return (
    <ClerkProvider>
      <html
        lang="en"
        className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      >
        <body className="min-h-full flex flex-col bg-background text-foreground">
          <header className="sticky top-0 z-10 flex justify-between items-center px-6 h-16 border-b border-border/60 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
            <div className="flex items-center gap-8">
              <Link href="/" className="font-semibold tracking-tight text-sm">
                AI-ETL
              </Link>
              <nav className="flex gap-6 text-sm text-muted-foreground">
                <Link href="/" className="hover:text-foreground transition-colors">
                  Executar
                </Link>
                <Link href="/historico" className="hover:text-foreground transition-colors">
                  Histórico
                </Link>
                <Link href="/pipelines" className="hover:text-foreground transition-colors">
                  Pipelines
                </Link>
              </nav>
            </div>
            <div className="flex items-center gap-1">
              <AgentsInfo modelName={modelName} />
              <AuthHeader />
            </div>
          </header>
          <div className="flex-1 flex flex-col">{children}</div>
          <Toaster richColors position="top-right" />
        </body>
      </html>
    </ClerkProvider>
  );
}
