import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const nextConfig: NextConfig = {
  /* config options here */
};

// Sprint 25 (ADR-036) — wires next-intl's server-component message loading into
// the build. Deliberately no i18n *routing* (no `/en`/`/pt-BR` URL prefix,
// no `localePrefix`/`pathnames` config): every existing route
// (`/`, `/app`, `/pipelines`, `/historico`, `/resumo`, `/comecar`, and every
// dynamic segment under them) keeps its current URL exactly as-is — this app
// already shipped and is bookmarked/linked at those paths, and `src/middleware.ts`
// already owns request-level routing for Clerk auth (`clerkMiddleware`). Locale is
// resolved per-request from a cookie (`src/i18n/request.ts`), not the URL, the same
// way `next-themes` resolves light/dark without a route segment either.
const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

export default withNextIntl(nextConfig);
