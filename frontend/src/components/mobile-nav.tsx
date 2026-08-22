"use client";

import { Menu } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

const NAV_LINKS = [
  { href: "/comecar", label: "Começar" },
  { href: "/app", label: "Executar" },
  { href: "/historico", label: "Histórico" },
  { href: "/pipelines", label: "Pipelines" },
  { href: "/resumo", label: "Resumo" },
];

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
export function MobileNav() {
  return (
    <Sheet>
      <SheetTrigger
        render={
          <Button variant="ghost" size="icon-sm" className="sm:hidden">
            <Menu />
            <span className="sr-only">Abrir menu</span>
          </Button>
        }
      />
      <SheetContent side="left">
        <SheetHeader>
          <SheetTitle>Menu</SheetTitle>
        </SheetHeader>
        <nav className="flex flex-col gap-1 px-4 pb-4">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="rounded-md px-2 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
            >
              {link.label}
            </Link>
          ))}
        </nav>
      </SheetContent>
    </Sheet>
  );
}
