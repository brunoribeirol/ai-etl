import Link from "next/link";
import { AuthHeader } from "@/components/auth-header";

/**
 * Marketing route group's own layout (landing page, no sprint number — see
 * docs/CURRENT_STATE.md). Deliberately minimal — no product nav
 * (Começar/Executar/Histórico/Pipelines/Resumo), those only make sense once
 * a visitor is signed in and inside `(app)`. `<AuthHeader>` still works here
 * unmodified: a signed-in visitor who lands back on `/` (e.g. via a bookmark)
 * sees their `<UserButton>`, same component `(app)/layout.tsx` uses.
 */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header className="sticky top-0 z-10 flex justify-between items-center px-6 h-16 border-b border-border/60 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <Link href="/" className="font-semibold tracking-tight text-sm">
          AI-ETL
        </Link>
        <AuthHeader />
      </header>
      <div className="flex-1 flex flex-col">{children}</div>
      <footer className="border-t border-border/60 px-6 py-8 text-xs text-muted-foreground">
        {/* No GitHub link here on purpose — the repo is private today (see
            docs/CURRENT_STATE.md); linking it would 404 for any visitor
            without access. Revisit if/when the repo goes public. */}
        <div className="max-w-5xl mx-auto text-center">
          AI-ETL — framework multiagente de ETL, código auditável.
        </div>
      </footer>
    </>
  );
}
