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

/** Returns the current user, or null if not logged in (a 401 is the normal, expected shape here —
 * every page checks this on mount, so it must not throw for "not logged in"). */
export async function me(): Promise<User | null> {
  try {
    return await request<User>("/api/v1/auth/me");
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}
