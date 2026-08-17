"use client";

import { SignInButton, UserButton, useUser } from "@clerk/nextjs";

/**
 * `<SignedIn>`/`<SignedOut>` were removed in `@clerk/nextjs` v7 ("Core 3") —
 * `useUser()` is the current replacement for this exact conditional-render
 * shape. Client Component (hooks need it), imported into the server-rendered
 * root layout — standard Next.js App Router composition.
 */
export function AuthHeader() {
  const { isLoaded, isSignedIn } = useUser();

  if (!isLoaded) {
    return null;
  }

  return isSignedIn ? <UserButton /> : <SignInButton />;
}
