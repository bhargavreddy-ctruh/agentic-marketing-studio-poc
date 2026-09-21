/**
 * Shared fetch/error-handling logic — extracted so `api.ts` (sessions) and `canvas.ts` don't each
 * carry their own copy of the same "parse the backend's typed-error JSON, throw a real ApiError"
 * block (the exact kind of duplication a real DRY audit already flagged once this session).
 */

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, init);
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

/** The real URL for the bytes behind a storage_ref (`GET /api/v1/canvas/assets/{storage_ref}`,
 * Phase 4b) — every other canvas endpoint returns metadata only. */
export function assetUrl(storageRef: string): string {
  return `${API_BASE_URL}/api/v1/canvas/assets/${storageRef}`;
}
