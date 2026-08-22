"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps } from "react";

/**
 * Sprint 38 — thin wrapper around `next-themes`' provider (already a
 * dependency since Sprint 7, but only ever wired into `sonner.tsx`'s toast
 * theming — the app itself stayed hardcoded `dark` on `<html>`). `attribute="class"`
 * matches `globals.css`'s `@custom-variant dark (&:is(.dark *))`, so toggling
 * here is exactly what already drove dark mode, just no longer hardcoded.
 * `defaultTheme="dark"` preserves the pre-Sprint-38 default look for anyone
 * with no stored preference yet; `enableSystem` additionally respects OS
 * preference once a visitor has never toggled manually.
 */
export function ThemeProvider({ children, ...props }: ComponentProps<typeof NextThemesProvider>) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="dark" enableSystem {...props}>
      {children}
    </NextThemesProvider>
  );
}
