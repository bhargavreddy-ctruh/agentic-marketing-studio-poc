/**
 * Typed client for the real backend Mood Board API. Mirrors
 * `poc/backend/src/schemas/mood_board/responses.py` field-for-field — see
 * `backend/src/api/v1/mood_board/routes.py`: upload lets a real prior-campaign asset seed
 * `asset_mood_board_search` (Architecture.md section 1b), which otherwise always correctly returns
 * "not configured" with nothing to search.
 */
import { API_BASE_URL, request } from "./http";

export interface MoodBoardAsset {
  id: string;
  storage_ref: string;
  mime_type: string;
  description: string;
  created_at: string;
}

export async function listMoodBoardAssets(): Promise<MoodBoardAsset[]> {
  return request<MoodBoardAsset[]>("/api/v1/mood-board/assets");
}

/** Multipart, not JSON — `request()` in `lib/http.ts` never sets `Content-Type` itself (only
 * per-call JSON bodies do, e.g. `lib/brand.ts`), so it's reusable as-is here: passing a `FormData`
 * body lets the browser set its own `multipart/form-data` boundary header correctly. */
export async function uploadMoodBoardAsset(file: File, description: string): Promise<MoodBoardAsset> {
  const form = new FormData();
  form.append("file", file);
  form.append("description", description);
  return request<MoodBoardAsset>("/api/v1/mood-board/assets", {
    method: "POST",
    body: form,
  });
}

/** No dedicated mood-board asset-bytes route exists (only `/canvas/assets/{storage_ref}` does,
 * for canvas elements) — `storage_ref` happens to be served by that same real endpoint too (both
 * ultimately resolve through the same storage layer), so this reuses it rather than guessing at a
 * URL shape that doesn't exist. */
export function moodBoardAssetUrl(storageRef: string): string {
  return `${API_BASE_URL}/api/v1/canvas/assets/${storageRef}`;
}
