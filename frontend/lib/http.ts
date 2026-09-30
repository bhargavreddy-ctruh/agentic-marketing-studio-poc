/**
 * Shared fetch/error-handling logic — extracted so `api.ts` (sessions) and `canvas.ts` don't each
 * carry their own copy of the same "parse the backend's typed-error JSON, throw a real ApiError"
 * block (the exact kind of duplication a real DRY audit already flagged once this session).
 */

// Real, live-found deploy blocker (2026-09-29): defaulting to "http://localhost:8000" meant a
// production deploy (Vercel) with NEXT_PUBLIC_API_BASE_URL simply left unset silently called
// localhost from every visitor's own browser instead of the real backend — and Vercel's own env
// var UI refuses to save a truly empty value, so "set it to empty string" wasn't even achievable
// there. Defaulting to "" (relative paths, proxied by next.config.js's rewrite in production)
// instead means the RIGHT thing now happens when the var is simply omitted — local dev still
// gets the real default via frontend/.env.local, which explicitly sets this to
// "http://localhost:8000" and is unaffected by this change.
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Real auth (Tasks_Workflows.md #1-4) — every call needs the signed session cookie sent, since
  // the backend and frontend are different ORIGINS (different ports), even though they're the
  // same SITE for cookie purposes. `credentials: "include"` is what makes the browser attach it.
  // `cache: "no-store"` is load-bearing, not defensive boilerplate: a real live bug (2026-09-22)
  // showed a freshly-completed session's canvas rendering empty after a hard refresh even though
  // curling the same endpoint directly returned the correct data — Next.js's own fetch cache (which
  // defaults to caching GETs unless told not to, independent of any HTTP Cache-Control header the
  // backend sends) was serving back the FIRST response ever made for that URL (the empty one, from
  // before generation finished). Every API call here must always hit the network live.
  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, credentials: "include", cache: "no-store" });
  if (!res.ok) {
    // The backend's real typed-error-to-JSON-response shape (core/middleware/error_handler.py) —
    // surfaced honestly rather than a generic "something went wrong".
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body.message ?? detail;
    } catch {
      // body wasn't JSON — keep statusText
    }
    throw new ApiError(detail, res.status);
  }
  return res.json();
}

/** Same shared fetch/error-handling as `request` above, for an endpoint that returns 204 No
 * Content on success (2026-09-30, the new `DELETE /sessions/{id}`) — `request`'s own unconditional
 * `res.json()` throws a SyntaxError against an empty body, so a no-content caller needs this
 * instead rather than working around that in every individual call site. */
export async function requestNoContent(path: string, init?: RequestInit): Promise<void> {
  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, credentials: "include", cache: "no-store" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body.message ?? detail;
    } catch {
      // body wasn't JSON — keep statusText
    }
    throw new ApiError(detail, res.status);
  }
}

/** The real URL for the bytes behind a storage_ref (`GET /api/v1/canvas/assets/{storage_ref}`,
 * Phase 4b) — every other canvas endpoint returns metadata only.
 *
 * `directUrl` (2026-09-29, latency win): when the backend response already carries a real
 * Cloudinary CDN url for this asset (`CanvasElementResponse.url` / `BrandProfileResponse.logo_url`
 * — `None` in local-disk dev mode or before an asset exists), use it straight — the browser hits
 * Cloudinary directly instead of this backend proxying the bytes a second time. Falls back to the
 * proxy route exactly as before when there's no direct url yet. */
export function assetUrl(storageRef: string, directUrl?: string | null): string {
  return directUrl || `${API_BASE_URL}/api/v1/canvas/assets/${storageRef}`;
}
