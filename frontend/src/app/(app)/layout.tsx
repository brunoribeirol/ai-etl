import { getTranslations } from "next-intl/server";
import Link from "next/link";
import { AgentsInfo } from "@/components/agents-info";
import { AppNav } from "@/components/app-nav";
import { AuthHeader } from "@/components/auth-header";
import { LocaleToggle } from "@/components/locale-toggle";
import { MobileNav } from "@/components/mobile-nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { apiFetch } from "@/lib/api";
import type { ApiConfig } from "@/lib/types";

/**
 * Sprint 7 redesign (ADR-011 surface, no route/auth contract change): dark
 * mode by default, shadcn Card header nav, moved verbatim from the old root
 * `layout.tsx` into this `(app)` route group's own layout when the landing
 * page (no sprint number, see docs/CURRENT_STATE.md) split marketing (`/`)
 * from product (`/app`, `/pipelines`, `/history`, `/summary`, `/get-started`):
 * every route in this group is still Clerk-protected by `proxy.ts`, same as
 * before the split.
 *
 * Sprint 38 mobile audit: the 5-link `<nav>` plus logo plus auth chrome
 * never wrapped or scrolled on narrow viewports: it just overflowed the
 * header. The horizontal `<nav>` is now `hidden` below `sm:` and replaced by
 * `<MobileNav>`'s hamburger + Sheet at that width; `<ThemeToggle>` is new
 * (light/dark toggle, same sprint).
 *
 * Sprint 25 (ADR-036): nav labels come from `messages/{locale}.json`'s
 * `appNav` namespace (`getTranslations`, this is an async Server Component,
 * same reason `apiFetch` above is awaited directly rather than via a hook).
 * `<LocaleToggle syncBackend>`: unlike the marketing layout's toggle, every
 * visitor here is signed in (`src/middleware.ts` protects this whole route
 * group), so flipping the UI locale also persists it as this tenant's
 * `users.locale` via `PATCH /tenant/locale` (ADR-036 §3).
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const tCommon = await getTranslations("common");

  // Best-effort: the model badge / "How it works" panel is informational,
  // not load-bearing: a config-fetch failure shouldn't break every page's
  // layout the way a failed page-level fetch would.
  let modelName: string | null = null;
  // Wave 6 (2026-08-25 admin panel/approval-gate UI plan): reuses this same
  // best-effort fetch to decide whether to show the "Admin" nav link.
  // Cosmetic only: `/admin` itself independently re-checks the role
  // server-side, and every admin API route enforces `require_admin` on its
  // own regardless of what the nav shows.
  let isAdmin = false;
  // Secrets UI (first consumer of api/routers/secrets.py): same cosmetic
  // gate, but at "editor" rank (`require_role("editor")` accepts editor or
  // admin, see `api/deps.py::_ROLE_RANK`), so a plain "viewer" never sees
  // this link even though it independently re-checks server-side too.
  let isEditor = false;
  try {
    const config = await apiFetch<ApiConfig>("/config");
    modelName = config.model_name;
    isAdmin = config.role === "admin";
    isEditor = config.role === "editor" || config.role === "admin";
  } catch {
    modelName = null;
  }

  return (
    <>
      <header className="sticky top-0 z-10 flex justify-between items-center px-4 sm:px-6 h-16 border-b border-border/60 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="flex items-center gap-2 lg:gap-6 min-w-0">
          <MobileNav isAdmin={isAdmin} isEditor={isEditor} />
          <Link href="/app" className="flex shrink-0 items-center gap-2 text-sm font-semibold tracking-tight">
            <span aria-hidden className="size-2 rounded-full bg-brand" />
            {tCommon("brand")}
          </Link>
          <AppNav isAdmin={isAdmin} isEditor={isEditor} />
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
