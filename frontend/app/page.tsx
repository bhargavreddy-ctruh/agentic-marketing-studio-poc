"use client";

import { useState } from "react";
import ChatPanel from "@/components/ChatPanel";
import CanvasView, { ReferencedElement } from "@/components/CanvasView";
import NodeGraphView from "@/components/NodeGraphView";
import { LiveEvent } from "@/lib/events";

type OutputMode = "canvas" | "node";

/**
 * One infinite canvas with chat floating on top of it — not two separate pages (Luma Labs'
 * reference UI, per the user's explicit direction). The canvas fills the whole screen and is
 * always visible; the chat panel is a floating card the user converses through, and whatever it
 * produces appears as real tiles on the same canvas behind it, without ever navigating away.
 *
 * "Output mode" (2026-09-21, per the user's explicit ask) switches that main view between the
 * canvas above and Node Mode — the real pipeline running node by node, live — without touching
 * chat at all; both views watch the same session.
 */
export default function Home() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [refreshSignal, setRefreshSignal] = useState(0);
  // "Reference an element in chat" — owned here since both the canvas (to highlight the tile) and
  // the chat panel (to show the chip and send it with the next message) need the same value.
  const [referencedElement, setReferencedElement] = useState<ReferencedElement | null>(null);
  const [outputMode, setOutputMode] = useState<OutputMode>("canvas");
  // The current/most recently completed turn's real events, for Node Mode — reset on a real
  // `turn_started` (every turn always emits exactly one, first), so a finished turn's real graph
  // stays visible until the next one actually begins, rather than clearing the instant it ends.
  const [turnEvents, setTurnEvents] = useState<LiveEvent[]>([]);

  function handleTurnEvent(event: LiveEvent) {
    setTurnEvents((prev) => (event.type === "turn_started" ? [event] : [...prev, event]));
  }

  return (
    <div className="relative h-screen w-screen overflow-hidden">
      <div className="absolute inset-0">
        {outputMode === "canvas" ? (
          <CanvasView
            sessionId={sessionId}
            refreshSignal={refreshSignal}
            referencedElementId={referencedElement?.id ?? null}
            onReferenceElement={setReferencedElement}
          />
        ) : (
          <NodeGraphView events={turnEvents} />
        )}
      </div>

      {/* Top-left "Output mode" control — switches the main view, chat is unaffected. */}
      <div className="pointer-events-none absolute left-4 top-4 z-20">
        <div className="pointer-events-auto flex items-center gap-1 rounded-full border border-neutral-200 bg-white/95 p-1 text-xs font-medium shadow-lg backdrop-blur">
          <button
            onClick={() => setOutputMode("canvas")}
            className={`rounded-full px-3 py-1.5 transition ${
              outputMode === "canvas" ? "bg-neutral-900 text-white" : "text-neutral-600 hover:bg-neutral-100"
            }`}
          >
            Canvas
          </button>
          <button
            onClick={() => setOutputMode("node")}
            className={`rounded-full px-3 py-1.5 transition ${
              outputMode === "node" ? "bg-neutral-900 text-white" : "text-neutral-600 hover:bg-neutral-100"
            }`}
          >
            Node
          </button>
        </div>
      </div>

      {/* Pinned bottom-right, a fixed 9:16 card — per the user's explicit layout request. */}
      <div className="pointer-events-none absolute bottom-4 right-4 z-10 aspect-[9/16] w-[340px] max-w-[calc(100vw-2rem)] max-h-[calc(100vh-2rem)]">
        <div className="pointer-events-auto h-full w-full">
          <ChatPanel
            sessionId={sessionId}
            onSessionId={setSessionId}
            onGenerated={() => setRefreshSignal((n) => n + 1)}
            referencedElement={referencedElement}
            onClearReference={() => setReferencedElement(null)}
            onTurnEvent={handleTurnEvent}
          />
        </div>
      </div>
    </div>
  );
}
