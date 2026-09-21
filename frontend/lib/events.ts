/**
 * Live event streaming — Phase 4c. Wraps the real backend SSE route
 * (`GET /api/v1/sessions/{id}/events`, `core/events.py`) via the browser's native `EventSource`,
 * which already handles the `text/event-stream` framing — no manual fetch-streaming needed.
 *
 * Real, disclosed scope boundary carried over from the backend's own docstring: this only applies
 * to `POST /turns` (a client already knows the session_id and can open the stream just before that
 * call). It does NOT apply to the very first message (`startSession`) — the session_id isn't known
 * until that call returns, so genuinely live-watching the very first turn isn't possible.
 */
import { API_BASE_URL } from "./http";

export interface LiveEvent {
  type: string;
  [key: string]: unknown;
}

export function openEventStream(
  sessionId: string,
  onEvent: (event: LiveEvent) => void,
): () => void {
  const es = new EventSource(`${API_BASE_URL}/api/v1/sessions/${sessionId}/events`);
  es.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      // A malformed frame is a real, if unlikely, possibility — skip it rather than crash the
      // whole narration stream over one bad line.
    }
  };
  // EventSource auto-reconnects on a dropped connection by design; since a turn's stream ends
  // on its own (the server closes after `turn_completed`), a later reconnect attempt would just
  // hang against a finished turn — the caller closes this via the returned function once done.
  return () => es.close();
}

/** Turns one real backend event into one short, human-readable narration line — no event type
 * invented here that `core/events.py` doesn't actually emit. */
export function describeEvent(event: LiveEvent): string | null {
  switch (event.type) {
    case "ideation_started":
      return "💭 Thinking about the brief…";
    case "ideation_completed":
      return event.ready ? "💭 Brief is ready" : "💭 Still gathering details";
    case "route_decided":
      return event.target_specialist
        ? `🧭 Routing to ${event.route} → ${event.target_specialist}`
        : `🧭 Routing to ${event.route}`;
    case "lead_started":
      return `🎨 ${event.lead} started${event.revision ? " (revision)" : ""}`;
    case "lead_completed":
      return `✅ ${event.lead} completed`;
    case "lead_failed":
      return `❌ ${event.lead} failed — ${event.reason}`;
    case "specialist_started":
      return `🧑‍🎨 ${event.specialist} started`;
    case "specialist_completed":
      return `✅ ${event.specialist} done (${event.tool_call_count} tool call${event.tool_call_count === 1 ? "" : "s"})`;
    case "specialist_failed":
      return `❌ ${event.specialist} failed — ${event.reason}`;
    case "tool_call":
      return `${event.ok ? "🔧" : "⚠️"} ${event.specialist} called ${event.tool}`;
    default:
      return null; // turn_started/turn_completed are structural, not narration lines
  }
}
