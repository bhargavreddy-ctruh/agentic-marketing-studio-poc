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

/** One real pipeline stage, reconstructed purely from the real event stream — Node Mode's whole
 * data model. Nothing here is invented: every field comes from an event `core/events.py` (and,
 * for `thinking`, the real streamed `llm_delta` text) genuinely emits. */
export interface PipelineNode {
  id: string;
  label: string;
  kind: "ideation" | "orchestrator" | "lead" | "specialist";
  status: "pending" | "running" | "completed" | "failed";
  /** Real streamed text accumulated live as this node's own LLM call runs (2026-09-21, per the
   * user's explicit ask to show real LLM "thinking," not a placeholder). Empty until the first
   * `llm_delta` for this node arrives — never fabricated filler text. */
  thinking: string;
  /** A short, real summary once this node completes — e.g. the route it chose, or how many real
   * tool calls a specialist made. Never present before the node's own completion event. */
  output?: string;
  /** Real tool calls this node made, in the order they actually happened. */
  tools: { tool: string; ok: boolean }[];
  reason?: string;
}

function nodeIdentity(nodeName: string): { id: string; label: string; kind: PipelineNode["kind"] } {
  if (nodeName === "ideation") return { id: "ideation", label: "Ideation", kind: "ideation" };
  if (nodeName === "orchestrator" || nodeName === "specialist_classifier") {
    return { id: "orchestrator", label: "Orchestrator", kind: "orchestrator" };
  }
  return { id: `specialist:${nodeName}`, label: nodeName, kind: "specialist" };
}

/** Reconstructs the real pipeline's node-level state from the flat event stream — Node Mode's
 * only data source. Pure function of `events`, so a caller just re-derives this on every new
 * event rather than hand-maintaining separate mutable node state. */
export function buildPipelineNodes(events: LiveEvent[]): PipelineNode[] {
  const nodes = new Map<string, PipelineNode>();
  const order: string[] = [];

  function ensure(id: string, label: string, kind: PipelineNode["kind"]): PipelineNode {
    let node = nodes.get(id);
    if (!node) {
      node = { id, label, kind, status: "pending", thinking: "", tools: [] };
      nodes.set(id, node);
      order.push(id);
    }
    return node;
  }

  for (const event of events) {
    switch (event.type) {
      case "ideation_started":
        ensure("ideation", "Ideation", "ideation").status = "running";
        break;
      case "ideation_completed": {
        const n = ensure("ideation", "Ideation", "ideation");
        n.status = "completed";
        n.output = event.ready ? "Brief ready" : "Needs more detail";
        break;
      }
      case "route_decided": {
        const n = ensure("orchestrator", "Orchestrator", "orchestrator");
        n.status = "completed";
        n.output = event.target_specialist
          ? `${event.route} → ${event.target_specialist}`
          : String(event.route ?? "");
        break;
      }
      case "lead_started":
        ensure(`lead:${event.lead}`, String(event.lead), "lead").status = "running";
        break;
      case "lead_completed":
        ensure(`lead:${event.lead}`, String(event.lead), "lead").status = "completed";
        break;
      case "lead_failed": {
        const n = ensure(`lead:${event.lead}`, String(event.lead), "lead");
        n.status = "failed";
        n.reason = String(event.reason ?? "");
        break;
      }
      case "specialist_started":
        ensure(`specialist:${event.specialist}`, String(event.specialist), "specialist").status = "running";
        break;
      case "specialist_completed": {
        const n = ensure(`specialist:${event.specialist}`, String(event.specialist), "specialist");
        n.status = "completed";
        n.output = `${event.tool_call_count} tool call${event.tool_call_count === 1 ? "" : "s"}`;
        break;
      }
      case "specialist_failed": {
        const n = ensure(`specialist:${event.specialist}`, String(event.specialist), "specialist");
        n.status = "failed";
        n.reason = String(event.reason ?? "");
        break;
      }
      case "tool_call": {
        const n = ensure(`specialist:${event.specialist}`, String(event.specialist), "specialist");
        n.tools.push({ tool: String(event.tool ?? ""), ok: Boolean(event.ok) });
        break;
      }
      case "llm_delta": {
        const { id, label, kind } = nodeIdentity(String(event.node ?? ""));
        const n = ensure(id, label, kind);
        if (n.status === "pending") n.status = "running";
        n.thinking += String(event.text ?? "");
        break;
      }
      default:
        break;
    }
  }

  return order.map((id) => nodes.get(id)!);
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
