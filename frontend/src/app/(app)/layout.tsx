import Link from "next/link";
import { AgentsInfo } from "@/components/agents-info";
import { AuthHeader } from "@/components/auth-header";
import { apiFetch } from "@/lib/api";
import type { ApiConfig } from "@/lib/types";

/**
 * Sprint 7 redesign (ADR-011 surface, no route/auth contract change) — dark
 * mode by default, shadcn Card header nav, moved verbatim from the old root
 * `layout.tsx` into this `(app)` route group's own layout when the landing
 * page (no sprint number, see docs/CURRENT_STATE.md) split marketing (`/`)
 * from product (`/app`, `/pipelines`, `/historico`, `/resumo`, `/comecar`) —
 * every route in this group is still Clerk-protected by `proxy.ts`, same as
 * before the split.
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
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
    <>
      <header className="sticky top-0 z-10 flex justify-between items-center px-6 h-16 border-b border-border/60 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="flex items-center gap-8">
          <Link href="/app" className="font-semibold tracking-tight text-sm">
            AI-ETL
          </Link>
          <nav className="flex gap-6 text-sm text-muted-foreground">
            <Link href="/comecar" className="hover:text-foreground transition-colors">
              Começar
            </Link>
            <Link href="/app" className="hover:text-foreground transition-colors">
              Executar
            </Link>
            <Link href="/historico" className="hover:text-foreground transition-colors">
              Histórico
            </Link>
            <Link href="/pipelines" className="hover:text-foreground transition-colors">
              Pipelines
            </Link>
            <Link href="/resumo" className="hover:text-foreground transition-colors">
              Resumo
            </Link>
          </nav>
        </div>
        <div className="flex items-center gap-1">
          <AgentsInfo modelName={modelName} />
          <AuthHeader />
        </div>
      </header>
      <div className="flex-1 flex flex-col">{children}</div>
    </>
  );
}
