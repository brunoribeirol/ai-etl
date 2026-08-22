"use client";

import { SignInButton, UserButton, useUser } from "@clerk/nextjs";
import { Button } from "@/components/ui/button";

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
