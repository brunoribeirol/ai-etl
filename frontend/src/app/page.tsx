import { auth } from "@clerk/nextjs/server";

/**
 * PR 3 scope (Sprint 6, ADR-011): prove the full auth loop works end-to-end
 * against the real, deployed FastAPI API before building any real UI —
 * sign in via Clerk's hosted flow (middleware.ts already gates this whole
 * app), fetch `GET /runs` with the session token attached, render the raw
 * response. `getToken()`'s default session token is the same kind of JWT
 * `verify_session_token()` (backend, ADR-006) already validates via
 * JWKS/`iss` — no custom Clerk JWT template needed for this.
 *
 * Replaced by the real "Histórico" page in PR 5.
 */
export default async function Home() {
  const { getToken } = await auth();
  const token = await getToken();

  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (!apiUrl) {
    return (
      <main className="p-8">
        <p className="text-red-600">
          NEXT_PUBLIC_API_URL is not configured.
        </p>
      </main>
    );
  }

  let body: unknown;
  let status: number;
  try {
    const response = await fetch(`${apiUrl}/runs`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    status = response.status;
    body = await response.json();
  } catch (err) {
    return (
      <main className="p-8">
        <p className="text-red-600">
          Failed to reach the API at {apiUrl}: {String(err)}
        </p>
      </main>
    );
  }

  return (
    <main className="p-8 flex flex-col gap-4">
      <h1 className="text-xl font-semibold">AI-ETL — GET /runs (smoke test)</h1>
      <p className="text-sm text-gray-500">HTTP {status}</p>
      <pre className="bg-gray-100 dark:bg-gray-900 p-4 rounded text-sm overflow-auto">
        {JSON.stringify(body, null, 2)}
      </pre>
    </main>
  );
}
