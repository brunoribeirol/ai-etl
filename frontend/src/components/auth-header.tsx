"use client";

import { SignInButton, UserButton, useUser } from "@clerk/nextjs";
import { Button } from "@/components/ui/button";

/**
 * `<SignedIn>`/`<SignedOut>` were removed in `@clerk/nextjs` v7 ("Core 3") —
 * `useUser()` is the current replacement for this exact conditional-render
 * shape. Client Component (hooks need it), imported into the server-rendered
 * root layout — standard Next.js App Router composition.
 *
 * Sprint 7: `SignInButton` wraps a shadcn `Button` (`asChild`) instead of
 * rendering its own default markup, so it matches the rest of the app.
 */
export function AuthHeader() {
  const { isLoaded, isSignedIn } = useUser();

  if (!isLoaded) {
    return null;
  }

  return isSignedIn ? (
    <UserButton />
  ) : (
    <SignInButton>
      <Button size="sm">Entrar</Button>
    </SignInButton>
  );
}
