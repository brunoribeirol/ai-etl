"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

/**
 * Client-side authed fetch (Wave 6, 2026-08-25 admin panel/approval-gate UI
 * plan) — extracted from the `authedFetch` `useCallback` `pipelines-manager.tsx`
 * originally defined inline (Sprint 13/30, needs `useAuth().getToken()` fresh
 * per request, unlike the server-side `lib/api.ts::apiFetch`, which reads the
 * token via `auth()` at request time in a Server Component). First reuse
 * across more than one component (the admin panel and the approval queue
 * both need it), so this is now shared rather than duplicated a third time.
 *
 * Same error convention as `apiFetch`: throws `Error(body?.detail ?? "HTTP
 * {status}")`, `body` best-effort JSON-parsed.
 */
export function useAuthedFetch() {
  const { getToken } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;

  return useCallback(
    async <T>(path: string, init?: RequestInit): Promise<T> => {
      if (!apiUrl) {
        throw new Error("NEXT_PUBLIC_API_URL is not configured.");
      }
      const token = await getToken();
      const response = await fetch(`${apiUrl}${path}`, {
        ...init,
        headers: {
          Authorization: `Bearer ${token}`,
          ...(init?.body ? { "Content-Type": "application/json" } : {}),
          ...init?.headers,
        },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail ?? `HTTP ${response.status}`);
      }
      return response.json();
    },
    [apiUrl, getToken],
  );
}
