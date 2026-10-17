"use client";

import { Menu } from "@base-ui/react/menu";
import { ChevronDown } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

type NavKey =
  | "getStarted"
  | "run"
  | "history"
  | "pipelines"
  | "approvals"
  | "summary"
  | "budget"
  | "dataExport"
  | "secrets"
  | "admin";

// The five pages used in every session stay visible; the rest sit under "More". Before the
// 2026-10-16 redesign all ten links were inline from 640px up and overflowed the header at
// medium widths (seen on production at about 1,460px with the toggles and avatar).
const PRIMARY: { href: string; key: NavKey }[] = [
  { href: "/app", key: "run" },
  { href: "/history", key: "history" },
  { href: "/pipelines", key: "pipelines" },
  { href: "/summary", key: "summary" },
  { href: "/approvals", key: "approvals" },
];

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppNav({ isAdmin, isEditor }: { isAdmin: boolean; isEditor: boolean }) {
  const t = useTranslations("appNav");
  const pathname = usePathname();

  const more: { href: string; key: NavKey }[] = [
    { href: "/get-started", key: "getStarted" },
    { href: "/budget", key: "budget" },
    { href: "/data-export", key: "dataExport" },
    ...(isEditor ? [{ href: "/secrets", key: "secrets" as const }] : []),
    ...(isAdmin ? [{ href: "/admin", key: "admin" as const }] : []),
  ];
  const moreActive = more.some((item) => isActive(pathname, item.href));

  return (
    <nav className="hidden lg:flex items-center gap-1 text-sm" aria-label={t("label")}>
      {PRIMARY.map((item) => {
        const active = isActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "relative rounded-md px-3 py-1.5 transition-colors",
              active
                ? "text-foreground after:absolute after:inset-x-3 after:-bottom-[13px] after:h-px after:bg-brand"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t(item.key)}
          </Link>
        );
      })}
      <Menu.Root>
        <Menu.Trigger
          className={cn(
            "inline-flex items-center gap-1 rounded-md px-3 py-1.5 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
            moreActive ? "text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t("more")}
          <ChevronDown className="size-3.5" aria-hidden />
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Positioner sideOffset={10} align="start" className="z-50">
            <Menu.Popup className="min-w-48 rounded-md border border-border bg-popover p-1 text-sm text-popover-foreground shadow-lg outline-none">
              {more.map((item) => (
                <Menu.LinkItem
                  key={item.href}
                  render={<Link href={item.href} />}
                  className={cn(
                    "flex cursor-default rounded-sm px-3 py-2 outline-none data-[highlighted]:bg-accent",
                    isActive(pathname, item.href) && "text-brand-strong",
                  )}
                >
                  {t(item.key)}
                </Menu.LinkItem>
              ))}
            </Menu.Popup>
          </Menu.Positioner>
        </Menu.Portal>
      </Menu.Root>
    </nav>
  );
}
