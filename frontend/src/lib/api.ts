import { auth } from "@clerk/nextjs/server";

/**
 * Server-side fetch helper for `src/ai_etl/api/` (ADR-011) — the same
 * auth()/getToken()/fetch shape PR 3's smoke-test page proved works, shared
 * now that "/" and "/historico" both need it. Client Components needing
 * fresh-per-request tokens (e.g. polling) use `useAuth().getToken()` from
 * `@clerk/nextjs` directly instead — see `executar-form.tsx`.
 */
export async function apiFetch<T>(path: string): Promise<T> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (!apiUrl) {
    throw new Error("NEXT_PUBLIC_API_URL is not configured.");
  }

  const { getToken } = await auth();
  const token = await getToken();

  const response = await fetch(`${apiUrl}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `HTTP ${response.status}`);
  }

  return response.json();
}
