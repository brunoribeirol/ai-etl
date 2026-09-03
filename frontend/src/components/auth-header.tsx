"use client";

import dynamic from "next/dynamic";
import { SignInButton, useUser } from "@clerk/nextjs";
import { Button } from "@/components/ui/button";

// Lazy-loaded (2026-09-03 perf fix, same `next/dynamic`/`ssr: false` pattern
// already used for `plotly-chart.tsx`): a real page-load audit found
// `<UserButton>` alone pulling in `@clerk/ui`'s vendors/component bundles
// (tens of KB, 400-550ms each) on *every* authenticated page, just to render
// a small avatar dropdown that isn't part of the critical path. Deferring it
// lets the rest of the page (nav, content) render and become interactive
// first — the avatar pops in a beat later instead of blocking everything
// else on it. No `loading` fallback: an empty slot for a few hundred ms in
// the header's corner is less disruptive than a layout-shifting placeholder.
const UserButton = dynamic(() => import("@clerk/nextjs").then((mod) => mod.UserButton), {
  ssr: false,
});

/**
 * `<SignedIn>`/`<SignedOut>` were removed in `@clerk/nextjs` v7 ("Core 3") —
 * `useUser()` is the current replacement for this exact conditional-render
 * shape. Client Component (hooks need it), rendered inside `(app)/layout.tsx`
 * — this component only ever renders for an already-authenticated visitor in
 * practice (the whole `(app)` group is behind `proxy.ts`'s `auth.protect()`),
 * but keeps the loading/signed-out branches for the brief moment Clerk's
 * client-side session hydrates.
 *
 * `forceRedirectUrl="/app"`: explicit since the landing-page split (no sprint
 * number, see docs/CURRENT_STATE.md) moved the product off `/` — Clerk's own
 * un-configured default would otherwise send a fresh sign-in back to `/`,
 * which is now the public marketing page, not the app.
 */
export function AuthHeader() {
  const { isLoaded, isSignedIn } = useUser();

  if (!isLoaded) {
    return null;
  }

  return isSignedIn ? (
    <UserButton />
  ) : (
    <SignInButton forceRedirectUrl="/app">
      <Button size="sm">Entrar</Button>
    </SignInButton>
  );
}
