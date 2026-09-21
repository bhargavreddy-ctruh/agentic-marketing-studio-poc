/**
 * Typed client for the real backend session API — Phase 4a.
 *
 * Types here mirror `poc/backend/src/schemas/sessions/{requests,responses}.py` field-for-field,
 * not a guess: `IdeationPrompt`'s shape (message/options/allow_free_text) is the one real
 * "propose options + free text" pattern used across ideation AND every HITL gate, so this one
 * client type covers all of them — no separate shape per gate.
 */
import { ApiError, request } from "./http";

export { ApiError };

export interface IdeationOption {
  id: string;
  label: string;
  description: string;
}

export interface IdeationPrompt {
  message: string;
  options: IdeationOption[];
  allow_free_text: boolean;
}

/** Narrative Lead's staged plan (`services/leads/base.py`'s `NarrativePlan.to_dict()`) — present
 * in `SessionResponse.brief.narrative_plan` once a video pipeline has produced or approved one,
 * needed to render the real narrative/motion HITL gates (Phase 4d). */
export interface NarrativePlan {
  shots: string[];
  overall_story: string;
  script_line: string | null;
  pacing_target: string;
}

/** Scene Lead's staged plan (`ScenePlan.to_dict()`) — present in `brief.scene_plan`, needed for
 * the real scene/motion HITL gates. */
export interface ScenePlan {
  environment_description: string;
  prop_description: string | null;
  lighting_description: string;
  scene_image_storage_ref: string;
}

export interface SessionResponse {
  id: string;
  status: string;
  approval_mode: "auto" | "approve";
  brief: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  next_prompt: IdeationPrompt | null;
}

export async function startSession(
  initialMessage: string,
  approvalMode: "auto" | "approve" = "auto",
): Promise<SessionResponse> {
  return request<SessionResponse>("/api/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initial_message: initialMessage, approval_mode: approvalMode }),
  });
}

export async function postTurn(
  sessionId: string,
  args: { pickedOptionId?: string; freeText?: string; referencedElementId?: string },
): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/v1/sessions/${sessionId}/turns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      picked_option_id: args.pickedOptionId,
      free_text: args.freeText,
      referenced_element_id: args.referencedElementId,
    }),
  });
}

export async function getSession(sessionId: string): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/v1/sessions/${sessionId}`);
}
