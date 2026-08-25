"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";
import { Button } from "@/components/ui/button";

/**
 * Sprint 38 — light/dark toggle, accessible from every screen (mounted in
 * both `(app)/layout.tsx` and `(marketing)/layout.tsx` headers). Two-state
 * toggle (light/dark) rather than a light/dark/system menu — matches the
 * spec ("toggle light/dark funcional"), and `next-themes` still persists the
 * choice to `localStorage` under the hood either way.
 *
 * `mounted` guard: `next-themes` can't know the real theme during SSR (it
 * lives in `localStorage`), so `resolvedTheme` is undefined on the very
 * first client render. Rendering a disabled placeholder for that one frame
 * avoids a hydration mismatch without needing `suppressHydrationWarning`
 * here (the `<html>` tag already carries it, see `layout.tsx`).
 *
 * `useSyncExternalStore` instead of the old `useState(false)` +
 * `useEffect(() => setMounted(true), [])` pair (ESLint 10 /
 * eslint-plugin-react-hooks@7's `react-hooks/set-state-in-effect` now
 * flags that pattern's synchronous setState-in-effect as a cascading-render
 * risk): subscribing to a store that never changes and only differs between
 * the server snapshot (`false`) and the client snapshot (`true`) is the
 * React-team-documented replacement for this exact "has the client
 * hydrated yet" check, with no extra render-triggering state at all.
 */
function subscribeNoop() {
  return () => {};
}

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(
    subscribeNoop,
    () => true,
    () => false,
  );

  if (!mounted) {
    return (
      <Button variant="ghost" size="icon-sm" disabled aria-hidden>
        <Sun />
      </Button>
    );
  }

  const isDark = resolvedTheme === "dark";

  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      aria-label={isDark ? "Mudar para tema claro" : "Mudar para tema escuro"}
    >
      {isDark ? <Sun /> : <Moon />}
      <span className="sr-only">Alternar tema claro/escuro</span>
    </Button>
  );
}
