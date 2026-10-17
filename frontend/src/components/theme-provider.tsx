"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps } from "react";

/**
 * Sprint 38: thin wrapper around `next-themes`' provider (already a
 * dependency since Sprint 7, but only ever wired into `sonner.tsx`'s toast
 * theming: the app itself stayed hardcoded `dark` on `<html>`). `attribute="class"`
 * matches `globals.css`'s `@custom-variant dark (&:is(.dark *))`, so toggling
 * here is exactly what already drove dark mode, just no longer hardcoded.
 * `defaultTheme="light"` (dark until the 2026-10-16 redesign, whose warm paper palette is
 * light-first) applies to anyone with no stored preference yet; a stored choice still wins.
 * `enableSystem` keeps "system" available as a choice.
 */
export function ThemeProvider({ children, ...props }: ComponentProps<typeof NextThemesProvider>) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="light" enableSystem {...props}>
      {children}
    </NextThemesProvider>
  );
}
