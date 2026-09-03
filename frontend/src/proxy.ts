import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// Everything requires a signed-in session except the sign-in/sign-up pages
// themselves — Clerk's own account portal renders those, so this app never
// implements a login form. This is the whole point of Sprint 6 (ADR-011):
// no pasted token, no custom auth UI, just Clerk's own hosted flow.
//
// `/` (exact) added for the public landing page (no sprint number, see
// docs/CURRENT_STATE.md) — the product itself moved to `/app` and stays
// protected; only the marketing route group's own page is public. `/` is
// matched exactly (not `/(.*)`), so `/app`, `/pipelines`, etc. are unaffected.
const isPublicRoute = createRouteMatcher(["/", "/sign-in(.*)", "/sign-up(.*)"]);

export default clerkMiddleware(async (auth, req) => {
  if (!isPublicRoute(req)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    // Skip Next.js internals and static files, always run for API routes.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
