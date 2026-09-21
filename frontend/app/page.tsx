"use client";

import { useState } from "react";
import ChatPanel from "@/components/ChatPanel";
import CanvasView, { ReferencedElement } from "@/components/CanvasView";

/**
 * One infinite canvas with chat floating on top of it — not two separate pages (Luma Labs'
 * reference UI, per the user's explicit direction). The canvas fills the whole screen and is
 * always visible; the chat panel is a floating card the user converses through, and whatever it
 * produces appears as real tiles on the same canvas behind it, without ever navigating away.
 */
export default function Home() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [refreshSignal, setRefreshSignal] = useState(0);
  // "Reference an element in chat" — owned here since both the canvas (to highlight the tile) and
  // the chat panel (to show the chip and send it with the next message) need the same value.
  const [referencedElement, setReferencedElement] = useState<ReferencedElement | null>(null);

  return (
    <div className="relative h-screen w-screen overflow-hidden">
      <div className="absolute inset-0">
        <CanvasView
          sessionId={sessionId}
          refreshSignal={refreshSignal}
          referencedElementId={referencedElement?.id ?? null}
          onReferenceElement={setReferencedElement}
        />
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
          />
        </div>
      </div>
    </div>
  );
}
