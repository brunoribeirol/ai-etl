import { getTranslations } from "next-intl/server";
import Link from "next/link";
import { AgentsInfo } from "@/components/agents-info";
import { AuthHeader } from "@/components/auth-header";
import { LocaleToggle } from "@/components/locale-toggle";
import { MobileNav } from "@/components/mobile-nav";
import { ThemeToggle } from "@/components/theme-toggle";
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
 *
 * Sprint 38 mobile audit: the 5-link `<nav>` plus logo plus auth chrome
 * never wrapped or scrolled on narrow viewports — it just overflowed the
 * header. The horizontal `<nav>` is now `hidden` below `sm:` and replaced by
 * `<MobileNav>`'s hamburger + Sheet at that width; `<ThemeToggle>` is new
 * (light/dark toggle, same sprint).
 *
 * Sprint 25 (ADR-036) — nav labels come from `messages/{locale}.json`'s
 * `appNav` namespace (`getTranslations`, this is an async Server Component,
 * same reason `apiFetch` above is awaited directly rather than via a hook).
 * `<LocaleToggle syncBackend>` — unlike the marketing layout's toggle, every
 * visitor here is signed in (`src/middleware.ts` protects this whole route
 * group), so flipping the UI locale also persists it as this tenant's
 * `users.locale` via `PATCH /tenant/locale` (ADR-036 §3).
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const t = await getTranslations("appNav");
  const tCommon = await getTranslations("common");

  // Best-effort: the model badge / "Como funciona" panel is informational,
  // not load-bearing — a config-fetch failure shouldn't break every page's
  // layout the way a failed page-level fetch would.
  let modelName: string | null = null;
  // Wave 6 (2026-08-25 admin panel/approval-gate UI plan) — reuses this same
  // best-effort fetch to decide whether to show the "Admin" nav link.
  // Cosmetic only: `/admin` itself independently re-checks the role
  // server-side, and every admin API route enforces `require_admin` on its
  // own regardless of what the nav shows.
  let isAdmin = false;
  try {
    const config = await apiFetch<ApiConfig>("/config");
    modelName = config.model_name;
    isAdmin = config.role === "admin";
  } catch {
    modelName = null;
  }

  return (
    <>
      <header className="sticky top-0 z-10 flex justify-between items-center px-4 sm:px-6 h-16 border-b border-border/60 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="flex items-center gap-2 sm:gap-8 min-w-0">
          <MobileNav isAdmin={isAdmin} />
          <Link href="/app" className="font-semibold tracking-tight text-sm shrink-0">
            {tCommon("brand")}
          </Link>
          <nav className="hidden sm:flex gap-6 text-sm text-muted-foreground">
            <Link href="/comecar" className="hover:text-foreground transition-colors">
              {t("comecar")}
            </Link>
            <Link href="/app" className="hover:text-foreground transition-colors">
              {t("executar")}
            </Link>
            <Link href="/historico" className="hover:text-foreground transition-colors">
              {t("historico")}
            </Link>
            <Link href="/pipelines" className="hover:text-foreground transition-colors">
              {t("pipelines")}
            </Link>
            <Link href="/aprovacoes" className="hover:text-foreground transition-colors">
              {t("aprovacoes")}
            </Link>
            <Link href="/resumo" className="hover:text-foreground transition-colors">
              {t("resumo")}
            </Link>
            {isAdmin && (
              <Link href="/admin" className="hover:text-foreground transition-colors">
                {t("admin")}
              </Link>
            )}
          </nav>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <LocaleToggle syncBackend />
          <ThemeToggle />
          <AgentsInfo modelName={modelName} />
          <AuthHeader />
        </div>
      </header>
      <div className="flex-1 flex flex-col">{children}</div>
    </>
  );
}
