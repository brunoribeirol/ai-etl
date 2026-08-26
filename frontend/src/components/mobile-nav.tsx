"use client";

import { Menu } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

// Sprint 25 (ADR-036): labels now come from `messages/{locale}.json`'s
// `appNav` namespace (shared with the horizontal `<nav>` in
// `(app)/layout.tsx`) — only the href/translation-key pairing stays here.
const NAV_LINKS = [
  { href: "/comecar", key: "comecar" },
  { href: "/app", key: "executar" },
  { href: "/historico", key: "historico" },
  { href: "/pipelines", key: "pipelines" },
  { href: "/aprovacoes", key: "aprovacoes" },
  { href: "/resumo", key: "resumo" },
] as const;

/**
 * Sprint 38 — mobile-responsiveness audit finding: `(app)/layout.tsx`'s
 * header packed logo + 5 nav links + "Como funciona" + auth into one
 * non-wrapping flex row (`justify-between items-center`, no `flex-wrap`,
 * no scroll fallback). Below ~640px the 5 links alone (~310px of text
 * plus `gap-6`) don't fit next to the logo and auth chrome, and nothing in
 * the original markup handled that — content overflowed the header,
 * dragging the whole page into unwanted horizontal scroll.
 *
 * Fix follows the pattern already established by `agents-info.tsx`
 * (Sprint 7): reuse the existing `Sheet` primitive rather than add a new
 * dependency. `(app)/layout.tsx` now shows the horizontal `<nav>` only at
 * `sm:` and up and swaps in this hamburger trigger below it.
 */
export function MobileNav({
  isAdmin = false,
  isEditor = false,
}: {
  isAdmin?: boolean;
  isEditor?: boolean;
}) {
  const t = useTranslations("appNav");
  const tCommon = useTranslations("common");

  // Wave 6 (2026-08-25 admin panel/approval-gate UI plan) — same cosmetic-
  // only role gate as the desktop `<nav>` in `(app)/layout.tsx`. Secrets UI
  // link is "editor" rank (accepts editor or admin), added before "Admin" so
  // both extra links land in the same relative order as the desktop nav.
  let links: readonly { href: string; key: string }[] = NAV_LINKS;
  if (isEditor) links = [...links, { href: "/segredos", key: "segredos" }];
  if (isAdmin) links = [...links, { href: "/admin", key: "admin" }];

  return (
    <Sheet>
      <SheetTrigger
        render={
          <Button variant="ghost" size="icon-sm" className="sm:hidden">
            <Menu aria-hidden="true" />
            <span className="sr-only">{tCommon("openMenu")}</span>
          </Button>
        }
      />
      <SheetContent side="left">
        <SheetHeader>
          <SheetTitle>{tCommon("menu")}</SheetTitle>
        </SheetHeader>
        <nav className="flex flex-col gap-1 px-4 pb-4">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="rounded-md px-2 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
            >
              {t(link.key)}
            </Link>
          ))}
        </nav>
      </SheetContent>
    </Sheet>
  );
}
