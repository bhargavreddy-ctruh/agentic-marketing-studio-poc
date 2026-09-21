"use client";

/**
 * Node Mode — Phase 4 follow-up (2026-09-21), per the user's explicit ask: an alternative to the
 * infinite canvas that shows the real pipeline running turn by turn — one card per real node
 * (Ideation, Orchestrator, each Lead, each specialist), each with its own real status, real
 * streamed "thinking" text, and the real tools it actually called. Everything here is derived
 * from `buildPipelineNodes()` (lib/events.ts), itself a pure reduction over the real SSE event
 * stream `core/events.py` emits — nothing on a card is fabricated or guessed.
 *
 * Deliberately not a pan/zoom canvas like `CanvasEngine.tsx` — a real scope boundary for this
 * first pass: a simple horizontal flex flow (wrapping on narrow viewports) with a plain connector
 * between consecutive real nodes, not the curved-SVG-path graph layout a reference mockup showed.
 * The reference's specific agent names (Strategist, Copywriter, Distribution) belong to a
 * different project — this view always shows this project's real pipeline names instead.
 */
import { LiveEvent, PipelineNode, buildPipelineNodes } from "@/lib/events";

const KIND_LABEL: Record<PipelineNode["kind"], string> = {
  ideation: "IDEATION",
  orchestrator: "ORCHESTRATOR",
  lead: "LEAD AGENT",
  specialist: "SPECIALIST AGENT",
};

const KIND_ICON: Record<PipelineNode["kind"], string> = {
  ideation: "💭",
  orchestrator: "🧭",
  lead: "🎬",
  specialist: "🧑‍🎨",
};

function StatusBadge({ status }: { status: PipelineNode["status"] }) {
  if (status === "completed") {
    return <span className="text-emerald-400">✓</span>;
  }
  if (status === "failed") {
    return <span className="text-red-500">✕</span>;
  }
  if (status === "running") {
    return <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-400" />;
  }
  return <span className="inline-block h-2 w-2 rounded-full bg-neutral-700" />;
}

function NodeCard({ node }: { node: PipelineNode }) {
  const borderColor =
    node.status === "failed"
      ? "border-red-800"
      : node.status === "running"
        ? "border-blue-700"
        : node.status === "completed"
          ? "border-emerald-900"
          : "border-neutral-800";

  return (
    <div
      className={`w-72 shrink-0 rounded-xl border ${borderColor} bg-neutral-900/90 p-4 text-neutral-200 shadow-lg`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span>{KIND_ICON[node.kind]}</span>
          <span className="font-semibold text-white">{node.label}</span>
        </div>
        <StatusBadge status={node.status} />
      </div>

      <div className="mt-3 min-h-[3rem] max-h-40 overflow-y-auto whitespace-pre-wrap text-xs text-neutral-400">
        {node.status === "pending" && "Waiting…"}
        {node.status === "failed" && (node.reason || "Failed")}
        {node.status !== "pending" && node.status !== "failed" && (node.thinking || node.output || "…")}
        {node.status === "completed" && node.thinking && node.output && (
          <p className="mt-1 text-neutral-300">{node.output}</p>
        )}
      </div>

      {node.tools.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1">
          {node.tools.map((t, i) => (
            <span
              key={i}
              className={`rounded-full px-2 py-0.5 text-[10px] ${
                t.ok ? "bg-neutral-800 text-neutral-300" : "bg-red-950 text-red-400"
              }`}
              title={t.ok ? "succeeded" : "failed"}
            >
              {t.tool}
            </span>
          ))}
        </div>
      )}

      <p className="mt-3 text-[10px] uppercase tracking-wide text-neutral-600">{KIND_LABEL[node.kind]}</p>
    </div>
  );
}

export default function NodeGraphView({ events }: { events: LiveEvent[] }) {
  const nodes = buildPipelineNodes(events);

  return (
    <div className="h-full w-full overflow-auto bg-neutral-950 p-8">
      {nodes.length === 0 ? (
        <p className="text-sm text-neutral-500">
          No pipeline activity yet — send a message in chat to see the real pipeline run here, node
          by node.
        </p>
      ) : (
        <div className="flex flex-wrap items-stretch gap-3">
          {nodes.map((node, i) => (
            <div key={node.id} className="flex items-stretch gap-3">
              <NodeCard node={node} />
              {i < nodes.length - 1 && (
                <div className="flex items-center text-neutral-700" aria-hidden>
                  ─▶
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
