import { useTranslations } from "next-intl";
import Link from "next/link";
import { AuthHeader } from "@/components/auth-header";
import { LocaleToggle } from "@/components/locale-toggle";
import { ThemeToggle } from "@/components/theme-toggle";

/**
 * Marketing route group's own layout. No product navigation: Run, History and the rest only
 * make sense once a visitor is signed in and inside `(app)`; the header links to the landing
 * page's own sections instead. `<AuthHeader>` works here unmodified: a signed-in visitor who
 * lands back on `/` sees their `<UserButton>`.
 *
 * `<ThemeToggle>` and `<LocaleToggle>` reach every screen, signed in or not. `syncBackend` is
 * left `false` here: a signed-out visitor has no tenant to configure, so the toggle only flips
 * the interface cookie.
 *
 * 2026-10-16 redesign: section links in the header, a footer with the product links and the
 * project it belongs to (still no repository link).
 */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  const t = useTranslations("marketingLayout");
  const tCommon = useTranslations("common");

  const sections = [
    { href: "#how-it-works", label: t("navHow") },
    { href: "#audit", label: t("navAudit") },
    { href: "#faq", label: t("navFaq") },
  ];

  return (
    <>
      <header className="sticky top-0 z-20 border-b border-border/70 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/65">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2 text-sm font-semibold tracking-tight">
            <span aria-hidden className="size-2 rounded-full bg-brand" />
            {tCommon("brand")}
          </Link>
          <nav className="hidden items-center gap-1 text-sm text-muted-foreground md:flex">
            {sections.map((section) => (
              <a
                key={section.href}
                href={section.href}
                className="rounded-md px-3 py-1.5 transition-colors hover:text-foreground"
              >
                {section.label}
              </a>
            ))}
          </nav>
          <div className="flex items-center gap-1">
            <LocaleToggle />
            <ThemeToggle />
            <AuthHeader />
          </div>
        </div>
      </header>
      <div className="flex flex-1 flex-col">{children}</div>
      <footer className="border-t border-border">
        <div className="mx-auto grid max-w-6xl gap-10 px-6 py-14 text-sm sm:grid-cols-[1.4fr_1fr_1.2fr]">
          <div className="flex flex-col gap-3">
            <span className="flex items-center gap-2 font-semibold tracking-tight">
              <span aria-hidden className="size-2 rounded-full bg-brand" />
              {tCommon("brand")}
            </span>
            <p className="max-w-xs text-muted-foreground">{t("footerTagline")}</p>
          </div>
          <div className="flex flex-col gap-3">
            <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
              {t("footerProduct")}
            </span>
            {sections.map((section) => (
              <a
                key={section.href}
                href={section.href}
                className="w-fit transition-colors hover:text-brand-strong"
              >
                {section.label}
              </a>
            ))}
          </div>
          <div className="flex flex-col gap-3">
            <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
              {t("footerProject")}
            </span>
            <p className="text-muted-foreground">{t("footerProjectBody")}</p>
          </div>
        </div>
      </footer>
    </>
  );
}
