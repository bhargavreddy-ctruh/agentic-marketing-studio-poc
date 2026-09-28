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
  // withCredentials: true — the events route is now ownership-checked (Tasks_Workflows.md #2),
  // so the signed session cookie must actually be sent; EventSource doesn't do this by default.
  const es = new EventSource(`${API_BASE_URL}/api/v1/sessions/${sessionId}/events`, {
    withCredentials: true,
  });
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
  /** Client-observed timestamps (`event._receivedAt`, stamped in page.tsx as each SSE event
   * arrives) — an honest proxy for real timing, not exact server-side instrumentation (the
   * backend's events carry no timestamp of their own). `startedAt` is set the first time this
   * node shows any activity; `endedAt` when it genuinely finishes (completed/failed). Used by
   * `assignLanes()` below to detect real overlap between nodes — Tasks.md #2. */
  startedAt?: number;
  endedAt?: number;
  /** True once a real `specialist_review_retry` event fires for this node (Tasks.md #3, surfaced
   * in Node Mode 2026-09-22) — a Lead genuinely caught this specialist's first attempt not
   * matching its own claim and sent it back with a correction, not a silent input->output call.
   * `endedAt` naturally ends up reflecting when the RETRY finished, not the first attempt — an
   * honest longer real duration for a card that needed two tries. */
  retried?: boolean;
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
  // Real turn separation (2026-09-22, per an explicit user ask: "show all the runs even after a
  // refresh") — restoring MULTIPLE past turns' persisted events concatenated into one array means
  // a bare id like "ideation" or "lead:visual_design_lead" would otherwise collide across turns,
  // and each later turn would silently overwrite the previous turn's card in place instead of
  // getting its own. Namespacing every id by which turn it belongs to (bumped on each real
  // `turn_started`) keeps every turn's own real run visible as its own set of cards.
  let turnIndex = -1;

  function ensure(rawId: string, label: string, kind: PipelineNode["kind"]): PipelineNode {
    const id = `t${turnIndex}:${rawId}`;
    let node = nodes.get(id);
    if (!node) {
      node = { id, label, kind, status: "pending", thinking: "", tools: [] };
      nodes.set(id, node);
      order.push(id);
    }
    return node;
  }

  // First activity stamps `startedAt`; a genuine finish stamps `endedAt` — both read from the
  // event's own client-receipt time (page.tsx), never `Date.now()` called here (this function
  // must stay a pure reduction over `events`, re-derivable identically on every render).
  function touchStart(n: PipelineNode, event: LiveEvent): void {
    if (n.startedAt == null && typeof event._receivedAt === "number") n.startedAt = event._receivedAt;
  }
  function touchEnd(n: PipelineNode, event: LiveEvent): void {
    if (typeof event._receivedAt === "number") n.endedAt = event._receivedAt;
  }

  for (const event of events) {
    switch (event.type) {
      case "turn_started": {
        turnIndex += 1;
        break;
      }
      case "ideation_started": {
        const n = ensure("ideation", "Ideation", "ideation");
        n.status = "running";
        touchStart(n, event);
        break;
      }
      case "ideation_completed": {
        const n = ensure("ideation", "Ideation", "ideation");
        n.status = "completed";
        n.output = event.ready ? "Brief ready" : "Needs more detail";
        touchStart(n, event);
        touchEnd(n, event);
        break;
      }
      case "route_decided": {
        const n = ensure("orchestrator", "Orchestrator", "orchestrator");
        n.status = "completed";
        n.output = event.target_specialist
          ? `${event.route} → ${event.target_specialist}`
          : String(event.route ?? "");
        touchStart(n, event);
        touchEnd(n, event);
        break;
      }
      case "lead_started": {
        const n = ensure(`lead:${event.lead}`, String(event.lead), "lead");
        n.status = "running";
        touchStart(n, event);
        break;
      }
      case "lead_completed": {
        const n = ensure(`lead:${event.lead}`, String(event.lead), "lead");
        n.status = "completed";
        touchStart(n, event);
        touchEnd(n, event);
        break;
      }
      case "lead_failed": {
        const n = ensure(`lead:${event.lead}`, String(event.lead), "lead");
        n.status = "failed";
        n.reason = String(event.reason ?? "");
        touchStart(n, event);
        touchEnd(n, event);
        break;
      }
      case "specialist_started": {
        const n = ensure(`specialist:${event.specialist}`, String(event.specialist), "specialist");
        n.status = "running";
        touchStart(n, event);
        break;
      }
      case "specialist_completed": {
        const n = ensure(`specialist:${event.specialist}`, String(event.specialist), "specialist");
        n.status = "completed";
        n.output = `${event.tool_call_count} tool call${event.tool_call_count === 1 ? "" : "s"}`;
        touchStart(n, event);
        touchEnd(n, event);
        break;
      }
      case "specialist_failed": {
        const n = ensure(`specialist:${event.specialist}`, String(event.specialist), "specialist");
        n.status = "failed";
        n.reason = String(event.reason ?? "");
        touchStart(n, event);
        touchEnd(n, event);
        break;
      }
      case "specialist_review_retry": {
        // Real Task 3 visibility (Tasks.md, 2026-09-22): a Lead genuinely caught this
        // specialist's first attempt not matching its own claim and sent it back corrected —
        // marked here so the card can show it, rather than the retry's second start/complete
        // pair silently overwriting the first with no sign a review ever happened. A visible
        // divider is inserted into the accumulated thinking text so the two attempts read as
        // two real turns, not one run-on stream.
        const n = ensure(`specialist:${event.specialist}`, String(event.specialist), "specialist");
        n.retried = true;
        n.status = "running";
        n.thinking += n.thinking ? "\n\n↻ reviewed — retrying with a correction…\n\n" : "";
        touchStart(n, event);
        break;
      }
      case "tool_call": {
        const n = ensure(`specialist:${event.specialist}`, String(event.specialist), "specialist");
        n.tools.push({ tool: String(event.tool ?? ""), ok: Boolean(event.ok) });
        touchStart(n, event);
        break;
      }
      case "llm_delta": {
        const { id, label, kind } = nodeIdentity(String(event.node ?? ""));
        const n = ensure(id, label, kind);
        if (n.status === "pending") n.status = "running";
        n.thinking += String(event.text ?? "");
        touchStart(n, event);
        break;
      }
      // Real, live-found bug (2026-09-24, per an explicit user report: "the node model has
      // latency its not showing the current status of nodes properly its taking atleast 30
      // seconds lag sometimes"): a real LLM provider retry/fallback (backend,
      // `_openai_compatible.py`/`router.py`) can genuinely take 20-30+ seconds, and until now
      // NOTHING was emitted during that wait — the currently-running node's card just sat
      // unchanged, looking frozen/stuck even though it genuinely was still working. Neither event
      // carries which node's own call is retrying (the retry logic is several layers below any
      // specialist/node identity) — attributed here to every node CURRENTLY "running" instead,
      // an honest approximation ("something active just hit this"), not a guess at exactly which.
      case "llm_retry": {
        const waitS = typeof event.wait_s === "number" ? event.wait_s.toFixed(1) : String(event.wait_s ?? "");
        const reason = event.reason === "rate_limited" ? "rate limited" : String(event.reason ?? "retrying");
        const note = `\n⏳ ${event.provider} ${reason} — retrying in ${waitS}s…`;
        for (const n of nodes.values()) {
          if (n.status === "running") {
            n.thinking += note;
            touchEnd(n, event); // keeps its visible "ongoing" duration honest, not stalled-looking
          }
        }
        break;
      }
      case "llm_provider_fallback": {
        const note = `\n🔁 ${event.from_provider} unavailable — switching to ${event.to_provider}…`;
        for (const n of nodes.values()) {
          if (n.status === "running") {
            n.thinking += note;
            touchEnd(n, event);
          }
        }
        break;
      }
      default:
        break;
    }
  }

  return order.map((id) => nodes.get(id)!);
}

/** One "lane" of nodes that don't genuinely overlap in time — nodes are packed into the first
 * lane whose last-placed node has already finished before this one started (classic interval
 * scheduling), so two nodes sharing a lane are guaranteed sequential, and two nodes that DO
 * overlap always land in different lanes. Nodes with no real timing (`startedAt` never stamped —
 * an event that arrived with no `_receivedAt`, or a node that only ever appeared in `tool_call`
 * without its own `_started`) fall back to one node per lane in event order, never guessed as
 * parallel with no real evidence. */
export interface LaneAssignment {
  node: PipelineNode;
  lane: number;
}

export function assignLanes(nodes: PipelineNode[]): LaneAssignment[] {
  const timed = nodes.filter((n) => n.startedAt != null);
  const untimed = nodes.filter((n) => n.startedAt == null);
  const sorted = [...timed].sort((a, b) => a.startedAt! - b.startedAt!);

  const laneEnds: number[] = []; // last real/ongoing end time occupying each lane
  const result: LaneAssignment[] = [];
  const now = Date.now();

  for (const node of sorted) {
    const start = node.startedAt!;
    const end = node.endedAt ?? now; // still running — treat as ongoing through "now"
    let lane = laneEnds.findIndex((laneEnd) => laneEnd <= start);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(end);
    } else {
      laneEnds[lane] = end;
    }
    result.push({ node, lane });
  }

  // Untimed nodes (real, just no receipt-time evidence) each get their own trailing lane —
  // visually sequential, honestly reflecting that no overlap was ever actually observed for them.
  let nextLane = laneEnds.length;
  for (const node of untimed) {
    result.push({ node, lane: nextLane });
    nextLane += 1;
  }

  return result;
}

/** Groups lane assignments into left-to-right "waves" for rendering — nodes in the same wave
 * share screen space as parallel columns; consecutive waves connect with an arrow. A wave is one
 * maximal run of nodes (in real start order) that keep landing in previously-unseen lanes without
 * a lane-reset — i.e. genuinely running at the same time as their wave-mates, per `assignLanes`'
 * own overlap detection, not just adjacent in the list. */
export function buildWaves(assignments: LaneAssignment[]): PipelineNode[][] {
  // Real, live-found bug (2026-09-22, independent review): untimed nodes each get a unique lane
  // from `assignLanes` (by design — "visually sequential"), but lanes never repeating among them
  // meant the loop below never saw a reason to start a new wave, so every untimed node ended up
  // clumped into a single wave together — rendering as falsely "parallel". Untimed nodes are
  // handled separately here, each forced into its own trailing singleton wave, matching the
  // documented intent; only real, timed nodes go through the lane-repeat wave-grouping logic.
  const timed = assignments.filter((a) => a.node.startedAt != null);
  const untimed = assignments.filter((a) => a.node.startedAt == null);
  const byStart = [...timed].sort((a, b) => a.node.startedAt! - b.node.startedAt!);

  const waves: PipelineNode[][] = [];
  let currentWave: LaneAssignment[] = [];
  let seenLanes = new Set<number>();

  for (const item of byStart) {
    if (currentWave.length > 0 && seenLanes.has(item.lane)) {
      // This lane already has a node in the current wave — a genuinely new wave starts (the
      // previous occupant of this lane must have already finished for this node to reuse it).
      waves.push(currentWave.map((a) => a.node));
      currentWave = [];
      seenLanes = new Set();
    }
    currentWave.push(item);
    seenLanes.add(item.lane);
  }
  if (currentWave.length > 0) waves.push(currentWave.map((a) => a.node));

  for (const item of untimed) waves.push([item.node]);
  return waves;
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
    case "specialist_review_retry":
      return `↻ ${event.specialist} reviewed — retrying with a correction`;
    case "tool_call":
      return `${event.ok ? "🔧" : "⚠️"} ${event.specialist} called ${event.tool}`;
    // Real, live-found bug (2026-09-24, per an explicit user report: "the node model has
    // latency its not showing the current status of nodes properly its taking atleast 30
    // seconds lag sometimes") — real Groq/OpenRouter rate limiting means a single LLM call can
    // genuinely spend 20-30+ seconds retrying, and NOTHING was ever emitted during that wait —
    // not a stale UI, a real silent gap. `llm_retry`/`llm_provider_fallback` (backend,
    // `_openai_compatible.py`/`router.py`) close it.
    case "llm_retry": {
      const waitS = typeof event.wait_s === "number" ? event.wait_s.toFixed(1) : String(event.wait_s ?? "");
      const reason = event.reason === "rate_limited" ? "rate limited" : String(event.reason ?? "retrying");
      return `⏳ ${event.provider} ${reason} — retrying ${event.model} in ${waitS}s…`;
    }
    case "llm_provider_fallback":
      return `🔁 ${event.from_provider} unavailable — switching to ${event.to_provider}…`;
    // Real, live-found gap (2026-09-26, parallel-dispatch backend update): the orchestrator's
    // dynamic plan can now genuinely run 2+ verified-independent steps concurrently
    // (`graph.py`'s `run_concurrent_specialists` usage) — this event fires right before dispatch.
    // No node-identity to attach it to yet (the group's own `specialist_started` events haven't
    // fired at this point), so it's narration-only here; the actual parallel VISUAL (side-by-side
    // waves) already renders automatically once those `specialist_started`/`_completed` events
    // land with real overlapping timestamps — `assignLanes`/`buildWaves` below were already built
    // to detect exactly that, no change needed there.
    case "dynamic_plan_group_parallel":
      return `⚡ Running ${event.step_count} steps in parallel…`;
    default:
      return null; // turn_started/turn_completed are structural, not narration lines
  }
}
