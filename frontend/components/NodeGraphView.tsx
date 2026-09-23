"use client";

/**
 * Node Mode — Phase 4 follow-up (2026-09-21), per the user's explicit ask: an alternative to the
 * infinite canvas that shows the real pipeline running turn by turn — one card per real node
 * (Ideation, Orchestrator, each Lead, each specialist), each with its own real status, real
 * streamed "thinking" text, and the real tools it actually called. Everything here is derived
 * from `buildPipelineNodes()` (lib/events.ts), itself a pure reduction over the real SSE event
 * stream `core/events.py` emits — nothing on a card is fabricated or guessed.
 *
 * Parallel branches (Tasks.md #2, 2026-09-22): nodes whose real observed running intervals
 * genuinely overlap (`assignLanes`/`buildWaves`, lib/events.ts — client-receipt timestamps, an
 * honest proxy since the backend's own events carry no timestamp) render stacked as a column of
 * parallel cards ("wave"); waves that never overlapped render left-to-right, connected by an
 * arrow.
 *
 * Real pan/zoom (2026-09-22, per an explicit user ask: "cannot zoom in zoom out or move canvas in
 * node mode add infinite canvas there also") — this used to be a plain scrollable div, a
 * deliberate scope decision at the time given how few concurrent branches Motion Lead's real
 * parallel execution actually produces. Now that this session accumulates EVERY turn's run history
 * (2026-09-22, `onRestoreEvents`), the content can genuinely outgrow a plain scroll area, so this
 * reuses the exact same `Camera`/`Viewport`/`fitViewport` math `CanvasEngine.tsx` already uses for
 * the real infinite canvas — same interaction feel (drag to pan, wheel to pan, ctrl/cmd+wheel to
 * zoom at the cursor), proven code, no new dependency.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Camera, Viewport, fitViewport } from "./canvas/camera";
import { LiveEvent, PipelineNode, assignLanes, buildPipelineNodes, buildWaves } from "@/lib/events";

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
          {node.retried && (
            <span
              className="rounded-full bg-amber-950 px-1.5 py-0.5 text-[10px] font-medium text-amber-400"
              title="A Lead caught this specialist's first attempt not matching its own claim and sent it back with a correction (Tasks.md #3)"
            >
              ↻ reviewed
            </span>
          )}
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

interface PanState {
  x: number;
  y: number;
  startCam: Viewport;
  moved: boolean;
}

export default function NodeGraphView({ events }: { events: LiveEvent[] }) {
  const nodes = useMemo(() => buildPipelineNodes(events), [events]);
  const waves = useMemo(() => buildWaves(assignLanes(nodes)), [nodes]);

  const rootRef = useRef<HTMLDivElement>(null);
  const worldRef = useRef<HTMLDivElement>(null);
  const camRef = useRef<Viewport>({ x: 0, y: 0, scale: 1 });
  const ptr = useRef<PanState | null>(null);
  const [, forceRender] = useState(0); // only used to sync React (e.g. the zoom % readout) after an imperative camera move

  const applyTransform = useCallback(() => {
    const c = camRef.current;
    if (worldRef.current) worldRef.current.style.transform = `translate(${c.x}px, ${c.y}px) scale(${c.scale})`;
  }, []);

  // A real, live-found bug (2026-09-22): the first version of this batched the `forceRender` that
  // keeps the zoom % readout in sync behind `requestAnimationFrame`, the same pattern
  // `CanvasEngine.tsx` uses — fine there (nothing reads the live scale back out into visible text),
  // but rAF callbacks are throttled or paused entirely for a backgrounded/non-visible tab, so the
  // readout could lag far behind or never catch up to the REAL camera state (confirmed live: the
  // DOM transform was already correct while the % text stayed stuck). `applyTransform` above stays
  // a direct, synchronous DOM mutation (the actual per-frame-critical path for smooth dragging);
  // the state bump for React's OWN re-render doesn't need rAF batching at all — React 18's
  // automatic batching already coalesces multiple calls within one event handler on its own.
  const moveCam = useCallback(
    (next: Viewport) => {
      camRef.current = next;
      applyTransform();
      forceRender((n) => n + 1);
    },
    [applyTransform],
  );

  const fitToContent = useCallback(() => {
    if (!rootRef.current || !worldRef.current) return;
    // `scrollWidth`/`scrollHeight` reflect the content's real, UNSCALED layout size regardless of
    // the current camera transform (CSS `transform: scale()` never affects layout size) — a
    // reliable "world box" with no need to track every card's own position separately, unlike
    // `CanvasEngine.tsx`'s absolutely-positioned tiles.
    const w = worldRef.current.scrollWidth;
    const h = worldRef.current.scrollHeight;
    if (!w || !h) return;
    const rect = rootRef.current.getBoundingClientRect();
    moveCam(fitViewport({ x: 0, y: 0, w, h }, rect.width, rect.height, 64));
  }, [moveCam]);

  // Fit once content first appears, and again whenever the real node COUNT changes (a new turn's
  // cards arrived) — never on every event (thinking text streaming in would otherwise re-fit on
  // every token and fight the user's own pan/zoom).
  const fittedFor = useRef<number>(-1);
  useEffect(() => {
    if (nodes.length > 0 && fittedFor.current !== nodes.length) {
      fittedFor.current = nodes.length;
      fitToContent();
    }
  }, [nodes.length, fitToContent]);

  function toLocal(clientX: number, clientY: number) {
    const rect = rootRef.current?.getBoundingClientRect();
    return { x: clientX - (rect?.left ?? 0), y: clientY - (rect?.top ?? 0) };
  }

  // Wheel: pan by default; ctrl/cmd+wheel (pinch/zoom gesture) zooms at the cursor — the exact same
  // feel as `CanvasEngine.tsx`'s own real canvas, for consistency across the app's two view modes.
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const cam = camRef.current;
      if (e.ctrlKey || e.metaKey) {
        const factor = Math.exp(-e.deltaY * 0.005);
        const local = toLocal(e.clientX, e.clientY);
        moveCam(new Camera(cam).zoomAt(local.x, local.y, factor));
        return;
      }
      moveCam({ ...cam, x: cam.x - e.deltaX, y: cam.y - e.deltaY });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [moveCam]);

  function handlePointerDown(e: React.PointerEvent) {
    if (e.button === 2) return;
    if ((e.target as HTMLElement).closest("button, select, input, a")) return;
    ptr.current = { x: e.clientX, y: e.clientY, startCam: { ...camRef.current }, moved: false };
    rootRef.current?.setPointerCapture(e.pointerId);
  }

  function handlePointerMove(e: React.PointerEvent) {
    const p = ptr.current;
    if (!p) return;
    const dx = e.clientX - p.x;
    const dy = e.clientY - p.y;
    if (Math.abs(dx) + Math.abs(dy) > 2) p.moved = true;
    if (p.moved) moveCam({ ...p.startCam, x: p.startCam.x + dx, y: p.startCam.y + dy });
  }

  function endGesture() {
    ptr.current = null;
  }

  function zoomByFactor(factor: number) {
    if (!rootRef.current) return;
    const rect = rootRef.current.getBoundingClientRect();
    moveCam(new Camera(camRef.current).zoomAt(rect.width / 2, rect.height / 2, factor));
  }

  return (
    <div
      ref={rootRef}
      className="relative h-full w-full touch-none overflow-hidden bg-neutral-950"
      style={{ cursor: ptr.current?.moved ? "grabbing" : "grab" }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endGesture}
      onPointerLeave={endGesture}
    >
      <div ref={worldRef} className="absolute left-0 top-0 origin-top-left p-8">
        {nodes.length === 0 ? (
          <p className="w-96 text-sm text-neutral-500">
            No pipeline activity yet — send a message in chat to see the real pipeline run here, node
            by node.
          </p>
        ) : (
          <div className="flex items-start gap-3">
            {waves.map((wave, i) => (
              <div key={i} className="flex items-start gap-3">
                <div className="flex flex-col gap-3">
                  {wave.length > 1 && (
                    <p className="text-[10px] font-semibold uppercase tracking-wide text-blue-500">
                      ⇉ running in parallel
                    </p>
                  )}
                  {wave.map((node) => (
                    <NodeCard key={node.id} node={node} />
                  ))}
                </div>
                {i < waves.length - 1 && (
                  <div className="flex h-full min-h-[6rem] items-center text-neutral-700" aria-hidden>
                    ─▶
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Zoom controls — same bottom-right placement convention as the canvas view's own controls
          elsewhere in this app, kept out of the way of the chat panel (bottom-right dock). */}
      {/* left-20, not left-4 (2026-09-22, a real live-found collision): Next.js's own dev-mode
          indicator badge sits fixed in the exact bottom-left corner and was intercepting clicks on
          the leftmost button here — confirmed live (zoom-out silently did nothing while zoom-in,
          further right, worked). Dev-build-only, but real during actual local testing. */}
      <div className="pointer-events-none absolute bottom-4 left-20 z-10 flex items-center gap-1 rounded-full border border-neutral-800 bg-neutral-900/95 p-1 text-xs font-medium text-neutral-300 shadow-lg backdrop-blur">
        <button
          onClick={() => zoomByFactor(0.8)}
          className="pointer-events-auto rounded-full px-2.5 py-1.5 hover:bg-neutral-800"
          title="Zoom out"
        >
          −
        </button>
        <span className="pointer-events-none w-12 select-none text-center text-neutral-500">
          {Math.round(camRef.current.scale * 100)}%
        </span>
        <button
          onClick={() => zoomByFactor(1.25)}
          className="pointer-events-auto rounded-full px-2.5 py-1.5 hover:bg-neutral-800"
          title="Zoom in"
        >
          +
        </button>
        <button
          onClick={fitToContent}
          className="pointer-events-auto ml-1 rounded-full px-2.5 py-1.5 hover:bg-neutral-800"
          title="Fit to content"
        >
          Fit
        </button>
      </div>
    </div>
  );
}
