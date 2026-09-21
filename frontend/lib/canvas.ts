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
