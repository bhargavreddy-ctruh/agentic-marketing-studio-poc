/**
 * Typed client for the real backend Canvas API — Phase 4b.
 *
 * Types mirror `poc/backend/src/schemas/canvas/{requests,responses}.py` field-for-field. The three
 * real Human-in-the-Loop intervention paths (Architecture.md section 1c) each map to one function
 * here: `regenerateElement` (targeted regenerate, same specialist), `commentOnElement` (comment
 * resolution, may route to a different specialist), and asset upload + `directEdit` (client-side
 * pixel work, no model call).
 */
import { assetUrl, request } from "./http";

export { assetUrl };

export interface CanvasElement {
  id: string;
  session_id: string;
  element_type: string;
  produced_by_specialist: string;
  version: number;
  storage_ref: string | null;
  created_at: string;
  updated_at: string;
  pending_storage_ref: string | null;
  pending_action: string | null;
  last_comment: string | null;
  // The real compliance gate's status for this element's current version — runs as a real
  // background task right after generation, so an element can genuinely be "running" for a few
  // seconds after it first appears on canvas, not just eventually "passed"/"failed".
  compliance_status: "running" | "passed" | "failed" | "disabled";
  // Real, already-generated text (a shot list, a scene description, a creative brief) for
  // `element_type: "text"` elements — those have no `storage_ref` at all, since there's no binary
  // asset; the text itself IS the content (2026-09-22, matching the reference product's own
  // "Creative Brief / Shot List" canvas cards).
  text_content: string | null;
  // A real, short, honest label for what this element actually IS (2026-09-22, per an explicit
  // user ask: "label everything properly and relative to what's generated") — the actual image
  // /motion prompt, voiceover line, etc. the backend recorded for it (`canvas_mapper.py`), never
  // fabricated; null only if genuinely nothing was ever recorded for this element.
  description: string | null;
  // Real backend field (`backend/src/schemas/canvas/responses.py`'s `CanvasElementResponse`) that
  // was never declared here — a real `tsc` gap found in the 2026-09-23 frontend audit
  // (`CanvasView.tsx` already reads `el.alignment_warning` off the raw response, so it worked at
  // runtime; the type just never caught it). Currently always null end-to-end — a "Laya decision
  // model" warning that was never fully wired up on the backend — so this stays functionally
  // inert until that's built, but the type should match what the backend actually returns.
  alignment_warning: string | null;
  ad_spec_name?: string | null;
  safe_zone_pct?: number | null;
  // Canvas Grouping (2026-09-25, revised same day: a workflow IS one campaign — grouping is by
  // real Product DNA instead) — which real product this element belongs to on the canvas, and
  // which element (if any) it was generated from. All nullable — a pre-existing element, or one
  // with no real product signal, lands in the canvas's flat "Unassigned" group rather than a
  // fabricated grouping.
  product_id: string | null;
  product_name: string | null;
  parent_element_id: string | null;
}

export interface CanvasElementVersion {
  version: number;
  storage_ref: string;
  created_at: string;
}

export interface CanvasState {
  session_id: string;
  elements: CanvasElement[];
}

export async function getCanvasState(sessionId: string): Promise<CanvasState> {
  return request<CanvasState>(`/api/v1/canvas/${sessionId}`);
}

export async function regenerateElement(elementId: string, instruction?: string): Promise<CanvasElement> {
  return request<CanvasElement>(`/api/v1/canvas/elements/${elementId}/regenerate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruction }),
  });
}

export async function commentOnElement(elementId: string, text: string): Promise<CanvasElement> {
  return request<CanvasElement>(`/api/v1/canvas/elements/${elementId}/comments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
}

export async function approveEdit(elementId: string): Promise<CanvasElement> {
  return request<CanvasElement>(`/api/v1/canvas/elements/${elementId}/approve-edit`, { method: "POST" });
}

export async function rejectEdit(elementId: string): Promise<CanvasElement> {
  return request<CanvasElement>(`/api/v1/canvas/elements/${elementId}/reject-edit`, { method: "POST" });
}

export async function uploadAsset(file: Blob): Promise<{ storage_ref: string; mime_type: string }> {
  const form = new FormData();
  form.append("file", file);
  return request(`/api/v1/canvas/assets`, { method: "POST", body: form });
}

/** Places an already-uploaded asset directly onto the canvas as a brand-new element — the real
 * backend half of "Upload Media" / "New Image/Video/Audio" / "Paste" (right-click canvas menu,
 * 2026-09-22). Callers upload first via `uploadAsset`, then pass its `storage_ref` here. */
export async function createElement(sessionId: string, storageRef: string): Promise<CanvasElement> {
  return request<CanvasElement>(`/api/v1/canvas/${sessionId}/elements`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ storage_ref: storageRef }),
  });
}

/** Uploads a raw file/blob and immediately places it on the canvas as a new element — the one
 * real action every context-menu item (New Image/Video/Audio, Upload Media, Paste) reduces to,
 * just with a different accepted-type filter applied client-side before calling this. */
export async function uploadAndPlaceElement(sessionId: string, file: Blob): Promise<CanvasElement> {
  const { storage_ref } = await uploadAsset(file);
  return createElement(sessionId, storage_ref);
}

export async function directEdit(elementId: string, storageRef: string): Promise<CanvasElement> {
  return request<CanvasElement>(`/api/v1/canvas/elements/${elementId}/direct-edit`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ storage_ref: storageRef }),
  });
}

export async function listVersions(elementId: string): Promise<CanvasElementVersion[]> {
  return request<CanvasElementVersion[]>(`/api/v1/canvas/elements/${elementId}/versions`);
}

export async function undoElement(elementId: string): Promise<CanvasElement> {
  return request<CanvasElement>(`/api/v1/canvas/elements/${elementId}/undo`, { method: "POST" });
}

export async function redoElement(elementId: string): Promise<CanvasElement> {
  return request<CanvasElement>(`/api/v1/canvas/elements/${elementId}/redo`, { method: "POST" });
}

export async function maskedEditElement(
  elementId: string,
  instruction: string,
  maskStorageRef: string
): Promise<CanvasElement> {
  return request<CanvasElement>(`/api/v1/canvas/elements/${elementId}/masked-edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruction, mask_storage_ref: maskStorageRef }),
  });
}

export async function exportAllAdSpecs(elementId: string): Promise<{ element_id: string; exports: Record<string, any> }> {
  return request<{ element_id: string; exports: Record<string, any> }>(
    `/api/v1/canvas/elements/${elementId}/export-all-specs`,
    { method: "POST" }
  );
}
