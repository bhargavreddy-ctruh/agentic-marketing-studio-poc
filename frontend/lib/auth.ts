/**
 * Real (test-grade) auth client — Tasks_Workflows.md #1. Mirrors
 * `poc/backend/src/schemas/auth/{requests,responses}.py` field-for-field. No token handling here
 * at all — the backend sets/reads a signed, HttpOnly cookie itself; this file never touches it
 * directly, `lib/http.ts`'s `credentials: "include"` is what makes it travel.
 */
import { ApiError, request } from "./http";

export { ApiError };

export interface User {
  id: string;
  username: string;
  created_at: string;
}

export async function register(username: string, password: string): Promise<User> {
  return request<User>("/api/v1/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export async function login(username: string, password: string): Promise<User> {
  return request<User>("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export async function logout(): Promise<void> {
  await request("/api/v1/auth/logout", { method: "POST" });
}

// Real, live-found gap (2026-10-05): both `app/page.tsx` and `app/studio/[sessionId]/page.tsx`
// call `me()` on mount, and React Strict Mode's dev-only double-invoke doubles each of those again
// — live network logs showed up to 5 real `/api/v1/auth/me` requests for a single page load, each
// one a real round trip through the Vercel→backend proxy AND a connection checked out of the
// deliberately small, production-shared pool (`models/base.py`'s `pool_size=3, max_overflow=2`) —
// a real, measurable contributor to the pool exhaustion behind "Loading studio…" hanging/assets
// failing to load under load. A module-level in-flight promise (survives a component's Strict-
// Mode unmount/remount, since the module itself doesn't get torn down) coalesces any concurrent
// callers onto the SAME underlying request — cleared as soon as it settles, so a later, genuinely
// new check (e.g. after fixing a network issue and clicking retry) still makes a fresh call.
let inFlightMe: Promise<User | null> | null = null;

/** Returns the current user, or null if not logged in (a 401 is the normal, expected shape here —
 * every page checks this on mount, so it must not throw for "not logged in"). */
export async function me(): Promise<User | null> {
  if (inFlightMe) return inFlightMe;
  inFlightMe = (async () => {
    try {
      return await request<User>("/api/v1/auth/me");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return null;
      throw err;
    } finally {
      inFlightMe = null;
    }
  })();
  return inFlightMe;
}
