"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import ChatPanel, { ChatPanelHandle } from "@/components/ChatPanel";
import CanvasView, { ReferencedElement } from "@/components/CanvasView";
import NodeGraphView from "@/components/NodeGraphView";
import GuardrailsSection from "@/components/GuardrailsSection";
import DNASection from "@/components/DNASection";
import StyleLockModal from "@/components/StyleLockModal";
import { LiveEvent } from "@/lib/events";
import { ApiError, User, me } from "@/lib/auth";

type OutputMode = "canvas" | "node";

/**
 * The real studio — Tasks_Workflows.md #4. This is what `app/page.tsx` used to be, moved to a
 * real URL (`/studio/[sessionId]`) instead of living only in React state. That's the actual fix
 * for "refresh clears everything": the session id now survives a refresh because it's IN THE URL,
 * not lost the instant the tab reloads and every `useState` resets to its initial value.
 * `ChatPanel`'s own restore-on-mount effect (2026-09-22) handles pulling real current state back
 * once this page loads — see that component for the honest disclosed limit (current state
 * restores, not the full turn-by-turn transcript, since the backend never persisted one).
 */
export default function StudioPage() {
  const params = useParams<{ sessionId: string }>();
  const router = useRouter();
  const sessionId = params.sessionId;

  const [authChecked, setAuthChecked] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  // Real, live-found bug (2026-09-23 frontend audit), same as `app/page.tsx`: `me()` rethrows
  // anything besides a 401 (e.g. a real network failure when the backend is unreachable), which
  // was unhandled here — an infinite "Loading…" screen with no way out.
  const [authError, setAuthError] = useState<string | null>(null);
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [referencedElements, setReferencedElements] = useState<ReferencedElement[]>([]);
  const [outputMode, setOutputMode] = useState<OutputMode>("canvas");
  const [turnEvents, setTurnEvents] = useState<LiveEvent[]>([]);
  // A real loading silhouette on the canvas (2026-09-22, per an explicit user ask) — driven by the
  // SAME real SSE events Node Mode already uses, not a fake spinner: `turn_started` begins it,
  // `route_decided` reveals WHICH kind of element is actually being produced (so the silhouette
  // can match — an image icon, not a generic blob), `turn_completed`/`lead_failed` end it. `null`
  // kind (genuinely not yet known, or a `direct_fix` whose real output kind isn't decided until it
  // actually runs) still shows a real, honest "working" placeholder — never a wrong guess.
  const [generating, setGenerating] = useState(false);
  const [generatingKind, setGeneratingKind] = useState<"image" | "video" | "audio" | "text" | null>(null);
  const [showGuardrails, setShowGuardrails] = useState(false);
  const [showDna, setShowDna] = useState(false);
  const [showStyleLock, setShowStyleLock] = useState(false);
  const chatPanelRef = useRef<ChatPanelHandle>(null);

  async function checkAuth() {
    setAuthError(null);
    try {
      const current = await me();
      if (!current) {
        router.replace("/login");
        return;
      }
      setUser(current);
      setAuthChecked(true);
    } catch (err) {
      setAuthError(err instanceof ApiError ? err.message : "the server didn't respond.");
    }
  }

  useEffect(() => {
    checkAuth();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Real, persisted Node Mode history restored from the database (2026-09-22) — a full replace,
   * not an append. A real, live-found design mistake caught right after shipping: the FIRST
   * version of this restored and accumulated EVERY past turn's events, on the honest but wrong
   * assumption that "show the runs even after a refresh" meant the whole session's history should
   * always be visible — the actual result was a wall of dozens of tiny cards from turns that
   * finished hours ago, replacing the simple "just the current run" view that existed before. The
   * real ask was narrower: don't lose visibility into the run that was ACTUALLY interrupted by the
   * refresh. `ChatPanel.tsx` now only ever passes the LAST turn's events here (restored on mount,
   * and again once a turn still `"generating"` at mount time resolves) — this callback itself
   * stays a full replace either way. */
  function handleRestoreEvents(events: LiveEvent[]) {
    // No real wall-clock time survived persistence (only order did) — a synthetic 1ms-per-event
    // offset keeps each event's relative ORDER real and distinguishable (so lane/wave packing
    // still means something) without claiming a real duration nothing actually recorded.
    const base = Date.now() - events.length;
    setTurnEvents(events.map((e, i) => ({ ...e, _receivedAt: base + i })));
  }

  function handleTurnEvent(event: LiveEvent) {
    const stamped: LiveEvent = { ...event, _receivedAt: Date.now() };
    // Reset on turn_started, not append (reverted 2026-09-22 — see `handleRestoreEvents` above):
    // Node Mode shows only the CURRENT turn's real run, matching how it always worked before the
    // over-broad "accumulate everything" change.
    setTurnEvents((prev) => (event.type === "turn_started" ? [stamped] : [...prev, stamped]));

    if (event.type === "turn_started") {
      setGenerating(true);
      setGeneratingKind(null);
    } else if (event.type === "route_decided") {
      const route = typeof event.route === "string" ? event.route : "";
      if (route === "full_image") setGeneratingKind("image");
      else if (route === "full_video") setGeneratingKind("video");
      else if (route === "full_audio") setGeneratingKind("audio");
      // "direct_fix": leaves kind as null — genuinely could produce any real kind depending on
      // which specialist/tool it actually calls; an honest "working", not a guessed icon.
    } else if (event.type === "turn_completed" || event.type === "lead_failed") {
      setGenerating(false);
      setGeneratingKind(null);
    }
  }

  /** The canvas's right-click "New Image"/"New Video"/"New Audio" (2026-09-22) — a real
   * generation, not an upload, so it reuses the exact same turn machinery a typed chat message
   * uses (SSE narration, error handling, the orchestrator's own real classification) via
   * `ChatPanel`'s imperative handle, rather than the canvas inventing a second way to talk to the
   * backend. Phrased as "Generate a new X:" specifically because the orchestrator's own routing
   * prompt (`orchestrator.py`) distinguishes "the SAME asset adjusted" (direct_fix) from "a
   * DIFFERENT/NEW asset produced" (full_image/full_video/full_audio) by request wording, not just
   * by whether an element already exists — this phrasing lines up with that prompt's own examples
   * so it routes correctly even when a prior element is already on the canvas. "audio" reaches a
   * real backend route (orchestrator.py's "full_audio", added 2026-09-22 specifically so this
   * menu item has something real to call) — disclosed honest limit surfaced in the prompt text
   * itself: only a spoken voiceover is possible, never music. */
  function handleRequestGenerate(kind: "image" | "video" | "audio") {
    const prompts = {
      image: "Describe the new image you want:",
      video: "Describe the new video you want:",
      audio: "Describe the voiceover line you want spoken (music isn't supported):",
    };
    const description = window.prompt(prompts[kind], "");
    if (!description) return;
    chatPanelRef.current?.sendFreeText(`Generate a new ${kind}: ${description}`);
  }

  if (authError) {
    return (
      <div className="flex h-screen w-screen flex-col items-center justify-center gap-3 text-sm text-neutral-500">
        <p>Could not reach the backend: {authError}</p>
        <button
          onClick={() => checkAuth()}
          className="rounded-lg border border-surface-700/50 px-4 py-1.5 text-surface-200 hover:bg-surface-800/60"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!authChecked || !user) {
    return (
      <div className="flex h-screen w-screen items-center justify-center text-sm text-neutral-500">
        Loading…
      </div>
    );
  }

  return (
    <div className="relative h-screen w-screen overflow-hidden">
      <div className="absolute inset-0">
        {outputMode === "canvas" ? (
          <CanvasView
            sessionId={sessionId}
            refreshSignal={refreshSignal}
            referencedElementIds={referencedElements.map(e => e.id)}
            onReferenceElements={setReferencedElements}
            onRequestGenerate={handleRequestGenerate}
            pendingGeneration={generating ? { kind: generatingKind } : null}
          />
        ) : (
          <NodeGraphView events={turnEvents} />
        )}
      </div>

      {/* Top-left: back to workflows + the existing Canvas/Node output-mode toggle.
       * Real, live-found bug (2026-09-23 frontend audit, confirmed not a testing-tool artifact):
       * `animate-fade-in-up` on THIS `pointer-events-none` wrapper made its `pointer-events-auto`
       * children stop receiving real mouse clicks (confirmed via a controlled test — removing just
       * this one class, nothing else, made an identical click succeed) — every other
       * `pointer-events-none`/`auto`-split wrapper in this codebase (ChatPanel's own wrapper,
       * CanvasView's floating panels) either doesn't use this animation class or doesn't hit the
       * same split pattern, and both click fine. Dropped here; the one-time fade-in was cosmetic,
       * not worth real buttons being unclickable. */}
      <div className="pointer-events-none absolute left-6 top-6 z-20 flex items-center gap-3">
        <button
          onClick={() => router.push("/")}
          className="pointer-events-auto flex items-center gap-2 rounded-full border border-surface-700/50 bg-surface-900/40 px-4 py-2 text-sm font-medium text-surface-200 shadow-xl backdrop-blur-xl transition-all hover:-translate-y-0.5 hover:bg-surface-800/60 hover:text-white"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" /></svg>
          Workflows
        </button>
        <div className="pointer-events-auto flex items-center gap-1 rounded-full border border-surface-700/50 bg-surface-900/40 p-1 text-sm font-medium shadow-xl backdrop-blur-xl transition-all">
          <button
            onClick={() => setOutputMode("canvas")}
            className={`rounded-full px-4 py-1.5 transition-all duration-300 ${
              outputMode === "canvas" ? "bg-brand-500 text-white shadow-[0_0_15px_rgba(99,102,241,0.5)]" : "text-surface-300 hover:text-white"
            }`}
          >
            Canvas
          </button>
          <button
            onClick={() => setOutputMode("node")}
            className={`rounded-full px-4 py-1.5 transition-all duration-300 ${
              outputMode === "node" ? "bg-brand-500 text-white shadow-[0_0_15px_rgba(99,102,241,0.5)]" : "text-surface-300 hover:text-white"
            }`}
          >
            Node
          </button>
        </div>
        
        <div className="pointer-events-auto flex items-center gap-1 rounded-full border border-surface-700/50 bg-surface-900/40 p-1 text-sm font-medium shadow-xl backdrop-blur-xl transition-all">
          <button
            onClick={() => setShowGuardrails(true)}
            className="rounded-full px-4 py-1.5 transition-all duration-300 text-surface-300 hover:text-white hover:bg-surface-800"
          >
            Guardrails
          </button>
          <button
            onClick={() => setShowDna(true)}
            className="rounded-full px-4 py-1.5 transition-all duration-300 text-surface-300 hover:text-white hover:bg-surface-800"
          >
            DNA
          </button>
          <button
            onClick={() => setShowStyleLock(true)}
            className="rounded-full px-4 py-1.5 transition-all duration-300 text-brand-300 hover:text-white hover:bg-surface-800"
          >
            Style Lock 🔒
          </button>
        </div>
      </div>

      <StyleLockModal
        sessionId={sessionId}
        isOpen={showStyleLock}
        onClose={() => setShowStyleLock(false)}
      />

      {showGuardrails && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-2xl bg-surface-900 rounded-xl shadow-2xl border border-surface-700 h-[80vh]">
            <button 
              onClick={() => setShowGuardrails(false)} 
              className="absolute top-4 right-4 text-surface-400 hover:text-white z-10"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
            <GuardrailsSection sessionId={sessionId} />
          </div>
        </div>
      )}

      {showDna && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-2xl bg-surface-900 rounded-xl shadow-2xl border border-surface-700 h-[60vh]">
            <button 
              onClick={() => setShowDna(false)} 
              className="absolute top-4 right-4 text-surface-400 hover:text-white z-10"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
            <DNASection sessionId={sessionId} />
          </div>
        </div>
      )}

      <div className="pointer-events-none absolute bottom-4 left-4 z-10 aspect-[9/16] w-[340px] max-w-[calc(100vw-2rem)] max-h-[calc(100vh-2rem)]">
        <div className="pointer-events-auto h-full w-full">
          <ChatPanel
            ref={chatPanelRef}
            sessionId={sessionId}
            onSessionId={() => {}} // never called — this page always already has a real sessionId
            onGenerated={() => setRefreshSignal((n) => n + 1)}
            referencedElements={referencedElements}
            onClearReference={(id) => setReferencedElements(prev => id ? prev.filter(e => e.id !== id) : [])}
            onTurnEvent={handleTurnEvent}
            onRestoreEvents={handleRestoreEvents}
          />
        </div>
      </div>
    </div>
  );
}
