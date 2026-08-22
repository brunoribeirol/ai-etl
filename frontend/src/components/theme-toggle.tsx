"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
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
 */
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

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
