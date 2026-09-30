/**
 * Typed client for the real backend session API — Phase 4a.
 *
 * Types here mirror `poc/backend/src/schemas/sessions/{requests,responses}.py` field-for-field,
 * not a guess: `IdeationPrompt`'s shape (message/options/allow_free_text) is the one real
 * "propose options + free text" pattern used across ideation AND every HITL gate, so this one
 * client type covers all of them — no separate shape per gate.
 */
import { ApiError, request, requestNoContent, API_BASE_URL } from "./http";

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
  title: string;
  status: string;
  approval_mode: "auto" | "approve";
  guardrails_enabled: boolean;
  brief: Record<string, unknown>;
  style_ref_storage_ref?: string | null;
  style_seed?: number | null;
  created_at: string;
  updated_at: string;
  next_prompt: IdeationPrompt | null;
}

/** Creates the session row only — no turn run yet. Split from the first message (2026-09-21) so
 * the caller can open the SSE stream (`openEventStream`) for this id BEFORE sending the first
 * `postTurn`, the same way every later turn already streams. See the backend's
 * `SessionService.create_session` docstring for the live bug this fixes (turn 1's events used to
 * be emitted before anyone could listen, and were silently lost). `title` (Tasks_Workflows.md #2)
 * is the real, human-chosen workflow name shown on the home page's list — omitted falls back to
 * the model's own "Untitled workflow" default. Requires being logged in (the backend reads the
 * owning user from the session cookie, never from this request body). */
export async function createSession(
  approvalMode: "auto" | "approve" = "auto",
  title?: string,
): Promise<SessionResponse> {
  return request<SessionResponse>("/api/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approval_mode: approvalMode, title }),
  });
}

/** The current logged-in user's own workflows (Tasks_Workflows.md #2) — the home page's real
 * data source, newest-updated first (matches the backend's own ordering). */
export async function listSessions(): Promise<SessionResponse[]> {
  return request<SessionResponse[]>("/api/v1/sessions");
}

/** Rename a workflow (2026-09-30, explicit user ask: "add a delete/edit button on workflows") —
 * same shape as `updateApprovalMode` below. */
export async function updateSessionTitle(
  sessionId: string,
  title: string,
): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/v1/sessions/${sessionId}/title`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

/** Permanently deletes a workflow and everything on it (2026-09-30, explicit user ask) — the
 * backend cascades the real delete (canvas elements/versions, chat turns, generation jobs, tool
 * call logs) in one transaction; this call itself is NOT undoable, so every caller must confirm
 * with the user first (the home page's delete button does, via `window.confirm`). */
export async function deleteSession(sessionId: string): Promise<void> {
  return requestNoContent(`/api/v1/sessions/${sessionId}`, { method: "DELETE" });
}

export async function updateSessionStyle(
  sessionId: string,
  styleRefStorageRef: string | null,
  styleSeed: number | null
): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/v1/sessions/${sessionId}/style`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      style_ref_storage_ref: styleRefStorageRef,
      style_seed: styleSeed,
    }),
  });
}

/** Real, live-found gap (2026-09-24, per an explicit user ask: "in chat box user should be able
 * to select the mode(auto/approve mode)") — mode selection previously only existed on the home
 * page's "new workflow" form, before a session even existed; there was no way to change it for an
 * already-running conversation. */
export async function updateApprovalMode(
  sessionId: string,
  approvalMode: "auto" | "approve",
): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/v1/sessions/${sessionId}/approval-mode`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approval_mode: approvalMode }),
  });
}

/** Per-session guardrails on/off (2026-09-25) — same shape as `updateApprovalMode` above. Ported
 * from `poc/frontend/lib/api.ts`. */
export async function updateGuardrailsEnabled(
  sessionId: string,
  guardrailsEnabled: boolean,
): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/v1/sessions/${sessionId}/guardrails-enabled`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guardrails_enabled: guardrailsEnabled }),
  });
}

export async function postTurn(
  sessionId: string,
  args: {
    pickedOptionId?: string;
    freeText?: string;
    referencedElementIds?: string[];
    // Product Grouping (2026-09-25) — which of the session's already-known products this turn's
    // generated element(s) belong to. Always an existing real product id, never free text; a
    // genuinely new product is created through the existing chat-detection/manual-onboarding
    // paths. Omitted, the backend inherits the referenced element's own product, or leaves the
    // new element unassigned. Ported from `poc/frontend/lib/api.ts`.
    targetProductId?: string;
    // Phase 1 gap-close (2026-09-28, combined grouping plan) — the explicit "+ Start new
    // product" choice. Distinct from simply omitting `targetProductId`: omitted still lets the
    // backend infer (inherit the referenced element's own product), while this explicitly
    // defeats that inheritance even when an element IS referenced. See
    // `PostTurnRequest.start_new_product`'s own docstring (backend) for the full reasoning.
    startNewProduct?: boolean;
  },
): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/v1/sessions/${sessionId}/turns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      picked_option_id: args.pickedOptionId,
      free_text: args.freeText,
      referenced_element_ids: args.referencedElementIds,
      target_product_id: args.targetProductId,
      start_new_product: args.startNewProduct ?? false,
    }),
  });
}

export async function getSession(sessionId: string): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/v1/sessions/${sessionId}`);
}

/** One real, persisted chat turn (`ChatTurnResponse`, 2026-09-22) — `thinking_text` is the real
 * accumulated model text streamed live during that turn, null when nothing was ever streamed. */
export interface ChatTurn {
  id: string;
  user_text: string;
  thinking_text: string | null;
  assistant_text: string | null;
  created_at: string;
  // Real, persisted Node Mode run history for this turn (2026-09-22, per an explicit user ask:
  // "show all the runs even after a refresh") — see backend `models/chat_turn.py`'s own docstring.
  events: Record<string, unknown>[];
  referenced_elements?: {
    id: string;
    kind: "image" | "video" | "audio" | "text";
    url: string;
    description: string;
  }[];
}

/** Real, persisted chat history — the actual fix for a page refresh losing the conversation
 * (2026-09-22). Every prior turn, oldest first, each with its real user message, the real
 * "thinking" streamed live during it, and the real final response. */
export async function listTurns(sessionId: string): Promise<ChatTurn[]> {
  const turns = await request<ChatTurn[]>(`/api/v1/sessions/${sessionId}/turns`);
  for (const t of turns) {
    if (t.referenced_elements) {
      for (const el of t.referenced_elements) {
        if (el.url?.startsWith("/api/")) {
          el.url = `${API_BASE_URL}${el.url}`;
        }
      }
    }
  }
  return turns;
}

export async function cancelTurn(sessionId: string): Promise<{ cancelled: boolean }> {
  return request<{ cancelled: boolean }>(`/api/v1/sessions/${sessionId}/cancel`, {
    method: "POST",
  });
}

/** Product/Brand crawler (2026-09-28) — background-dispatched on the backend, returns
 * immediately; progress/results surface purely via the SSE `crawler_*` events already streamed
 * through the same event-stream helper turn narration uses. */
export async function crawlUrl(sessionId: string, url: string): Promise<{ status: string; url: string }> {
  return request<{ status: string; url: string }>(`/api/v1/sessions/${sessionId}/crawl`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
}
