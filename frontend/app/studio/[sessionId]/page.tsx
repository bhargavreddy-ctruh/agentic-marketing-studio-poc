"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import ChatPanel, { ChatPanelHandle } from "@/components/ChatPanel";
import CanvasView, { ReferencedElement } from "@/components/CanvasView";
import NodeGraphView from "@/components/NodeGraphView";
import GuardrailsSection from "@/components/GuardrailsSection";
import DNASection from "@/components/DNASection";
import StyleLockModal from "@/components/StyleLockModal";
import { LiveEvent } from "@/lib/events";
import { ApiError, User, me } from "@/lib/auth";
import { Spinner } from "@/components/Spinner";
import { CloseButton } from "@/components/CloseButton";
import { PromptModal } from "@/components/PromptModal";
import AgentHUD, { getActiveSpecialist } from "@/components/AgentHUD";
import { ThemeToggle } from "@/components/ThemeToggle";

type OutputMode = "canvas" | "node";

// CloseButton is now imported from @/components/CloseButton (shared across all modals)

// ── Modal wrapper with Esc-to-close (8e) ────────────────────────────────────
function Modal({
  isOpen,
  onClose,
  children,
  maxWidth = "max-w-2xl",
  height = "h-[80vh]",
}: {
  isOpen: boolean;
  onClose: () => void;
  children: React.ReactNode;
  maxWidth?: string;
  height?: string;
}) {
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className={`relative w-full ${maxWidth} rounded-xl border border-surface-700 bg-surface-900 shadow-2xl ${height}`}>
        <CloseButton onClose={onClose} />
        {children}
      </div>
    </div>
  );
}

/**
 * The real studio — Tasks_Workflows.md #4.
 */
export default function StudioPage() {
  const params = useParams<{ sessionId: string }>();
  const router = useRouter();
  const sessionId = params.sessionId;

  const [authChecked, setAuthChecked] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [referencedElements, setReferencedElements] = useState<ReferencedElement[]>([]);
  const [outputMode, setOutputMode] = useState<OutputMode>("canvas");
  const [turnEvents, setTurnEvents] = useState<LiveEvent[]>([]);
  const [generating, setGenerating] = useState(false);
  const [generatingKind, setGeneratingKind] = useState<"image" | "video" | "audio" | "text" | null>(null);
  const [showGuardrails, setShowGuardrails] = useState(false);
  const [showDna, setShowDna] = useState(false);
  const [showStyleLock, setShowStyleLock] = useState(false);
  const [showElementsDrawer, setShowElementsDrawer] = useState(false);
  const [elementsCount, setElementsCount] = useState<number | null>(null);
  const [isChatMaximized, setIsChatMaximized] = useState(false);
  const chatPanelRef = useRef<ChatPanelHandle>(null);

  // ── PromptModal state for handleRequestGenerate (replaces window.prompt — 8c) ───
  const [promptModal, setPromptModal] = useState<{
    kind: "image" | "video" | "audio";
    label: string;
  } | null>(null);

  const activeSpecialist = useMemo(() => {
    return getActiveSpecialist(turnEvents, generating, generatingKind);
  }, [turnEvents, generating, generatingKind]);

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

  function handleRestoreEvents(events: LiveEvent[]) {
    const base = Date.now() - events.length;
    setTurnEvents(events.map((e, i) => ({ ...e, _receivedAt: base + i })));
  }

  function handleTurnEvent(event: LiveEvent) {
    const stamped: LiveEvent = { ...event, _receivedAt: Date.now() };
    setTurnEvents((prev) => (event.type === "turn_started" ? [stamped] : [...prev, stamped]));

    if (event.type === "turn_started") {
      setGenerating(true);
      setGeneratingKind(null);
    } else if (event.type === "route_decided") {
      const route = typeof event.route === "string" ? event.route : "";
      if (route === "full_image") setGeneratingKind("image");
      else if (route === "full_video") setGeneratingKind("video");
      else if (route === "full_audio") setGeneratingKind("audio");
    } else if (event.type === "turn_completed" || event.type === "lead_failed") {
      setGenerating(false);
      setGeneratingKind(null);
    }
  }

  function handleRequestGenerate(kind: "image" | "video" | "audio") {
    const labels = {
      image: "Describe the new image you want:",
      video: "Describe the new video you want:",
      audio: "Describe the voiceover line you want spoken (music isn't supported):",
    };
    // Open the PromptModal instead of the native window.prompt() — same behaviour,
    // styled to match the studio dark theme (spec 8c).
    setPromptModal({ kind, label: labels[kind] });
  }

  function handlePromptConfirm(description: string) {
    if (!promptModal) return;
    chatPanelRef.current?.sendFreeText(`Generate a new ${promptModal.kind}: ${description}`);
    setPromptModal(null);
  }

  // ── Auth error / loading ───────────────────────────────────────────────────
  if (authError) {
    return (
      <div className="flex h-screen w-screen flex-col items-center justify-center gap-4">
        <p className="text-sm text-surface-400">Could not reach the backend: {authError}</p>
        <button
          onClick={() => checkAuth()}
          className="rounded-lg border border-surface-700/50 px-4 py-2 text-sm text-surface-200 transition-colors hover:bg-surface-800/60"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!authChecked || !user) {
    return (
      <div className="flex h-screen w-screen items-center justify-center">
        <div className="flex items-center gap-3 text-sm text-surface-400">
          <Spinner size={5} />
          Loading studio…
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-surface-950 transition-colors duration-200">

      {/* 3d — Generating shimmer progress bar at very top edge */}
      {generating && (
        <div className="absolute inset-x-0 top-0 z-50 h-0.5 overflow-hidden">
          <div className="h-full w-1/3 animate-shimmer bg-gradient-to-r from-transparent via-brand-500 to-transparent" />
        </div>
      )}

      {/* Canvas / Node view fills the full screen */}
      <div className="absolute inset-0">
        {outputMode === "canvas" ? (
          <CanvasView
            sessionId={sessionId}
            refreshSignal={refreshSignal}
            referencedElementIds={referencedElements.map(e => e.id)}
            onReferenceElements={setReferencedElements}
            onRequestGenerate={handleRequestGenerate}
            pendingGeneration={generating ? { kind: generatingKind } : null}
            activeSpecialist={activeSpecialist}
            showElements={showElementsDrawer}
            onToggleElements={() => setShowElementsDrawer((s) => !s)}
            onElementsCountChange={setElementsCount}
          />
        ) : (
          <NodeGraphView events={turnEvents} />
        )}
      </div>

      {/* ── Studio Top Header Bar (Unified Flexbox, Responsive, Zero Overlap) ── */}
      <header
        className={`pointer-events-none absolute top-3 sm:top-4 z-20 flex items-center justify-between gap-1.5 sm:gap-2 lg:gap-3 transition-all duration-300 ease-in-out ${
          isChatMaximized ? "left-[376px] right-3 sm:right-5" : "left-3 sm:left-5 right-3 sm:right-5"
        }`}
      >
        {/* Left: Workflows + View Mode Toggle */}
        <div className="flex items-center gap-1.5 sm:gap-2 shrink-0">
          <button
            onClick={() => router.push("/")}
            className="pointer-events-auto flex items-center gap-1 sm:gap-1.5 rounded-full border border-surface-700/50 bg-surface-900/40 px-2.5 sm:px-3 py-1 sm:py-1.5 text-xs font-medium text-surface-200 shadow-xl backdrop-blur-xl transition-all hover:-translate-y-0.5 hover:bg-surface-800/60 hover:text-white"
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            <span className="hidden sm:inline">Workflows</span>
          </button>

          <div className="pointer-events-auto flex items-center gap-0.5 rounded-full border border-surface-700/50 bg-surface-900/40 p-0.5 text-xs font-medium shadow-xl backdrop-blur-xl">
            <button
              onClick={() => setOutputMode("canvas")}
              className={`rounded-full px-2.5 sm:px-3 py-1 transition-all duration-300 ${
                outputMode === "canvas"
                  ? "bg-brand-500 text-white shadow-[0_0_15px_rgba(99,102,241,0.5)]"
                  : "text-surface-300 hover:text-white"
              }`}
            >
              Canvas
            </button>
            <button
              onClick={() => setOutputMode("node")}
              className={`rounded-full px-2.5 sm:px-3 py-1 transition-all duration-300 ${
                outputMode === "node"
                  ? "bg-brand-500 text-white shadow-[0_0_15px_rgba(99,102,241,0.5)]"
                  : "text-surface-300 hover:text-white"
              }`}
            >
              Node
            </button>
          </div>
        </div>

        {/* Center: Live Agent HUD */}
        <div className="flex items-center justify-center min-w-0 mx-auto px-1">
          <AgentHUD
            events={turnEvents}
            generating={generating}
            generatingKind={generatingKind}
          />
        </div>

        {/* Right: Elements + Guardrails / DNA / Style Lock + User Avatar */}
        <div className="flex items-center gap-1 sm:gap-1.5 shrink-0">
          {outputMode === "canvas" && (
            <button
              onClick={() => setShowElementsDrawer((s) => !s)}
              className={`pointer-events-auto flex items-center gap-1 rounded-full border border-surface-700/50 px-2.5 py-1 text-xs font-medium shadow-xl backdrop-blur-xl transition-all ${
                showElementsDrawer
                  ? "bg-brand-500 text-white shadow-[0_0_15px_rgba(99,102,241,0.5)]"
                  : "bg-surface-900/40 text-surface-300 hover:bg-surface-800 hover:text-white"
              }`}
            >
              <span>Elements</span>
              {elementsCount !== null && (
                <span
                  className={`rounded-full px-1.5 py-0.2 text-[10px] ${
                    showElementsDrawer ? "bg-white/20 text-white" : "bg-surface-800 text-surface-400"
                  }`}
                >
                  {elementsCount}
                </span>
              )}
            </button>
          )}

          <div className="pointer-events-auto flex items-center gap-0.5 rounded-full border border-surface-700/50 bg-surface-900/40 p-0.5 text-xs font-medium shadow-xl backdrop-blur-xl">
            <button
              onClick={() => setShowGuardrails(true)}
              className="rounded-full px-2 sm:px-2.5 py-1 text-xs text-surface-300 transition-all hover:bg-surface-800 hover:text-white"
            >
              Guardrails
            </button>
            <button
              onClick={() => setShowDna(true)}
              className="rounded-full px-1.5 sm:px-2 py-1 text-xs text-surface-300 transition-all hover:bg-surface-800 hover:text-white"
            >
              DNA
            </button>
            <button
              onClick={() => setShowStyleLock(true)}
              className="flex items-center gap-1 rounded-full px-1.5 sm:px-2 py-1 text-xs text-brand-300 transition-all hover:bg-surface-800 hover:text-white"
            >
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
              <span className="hidden xl:inline">Style Lock</span>
              <span className="xl:hidden">Style</span>
            </button>
          </div>

          <div className="pointer-events-auto flex items-center gap-1 rounded-full border border-surface-700/50 bg-surface-900/40 py-1 pl-1 pr-2 text-xs font-medium text-surface-300 shadow-xl backdrop-blur-xl">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-500 text-[10px] font-semibold text-white">
              {user.username[0].toUpperCase()}
            </span>
            <span className="hidden xl:inline">{user.username}</span>
          </div>

          {/* Theme Toggle (Light / Dark) */}
          <ThemeToggle />
        </div>
      </header>


      {/* ── Modals with Esc-to-close (3e + 8e) ─────────────────────────────── */}
      <StyleLockModal
        sessionId={sessionId}
        isOpen={showStyleLock}
        onClose={() => setShowStyleLock(false)}
      />

      <Modal isOpen={showGuardrails} onClose={() => setShowGuardrails(false)} maxWidth="max-w-2xl" height="h-[80vh]">
        <GuardrailsSection sessionId={sessionId} />
      </Modal>

      <Modal isOpen={showDna} onClose={() => setShowDna(false)} maxWidth="max-w-2xl" height="h-[60vh]">
        <DNASection sessionId={sessionId} />
      </Modal>

      {/* ── PromptModal for right-click canvas generate (replaces window.prompt — 8c) ── */}
      <PromptModal
        isOpen={promptModal !== null}
        label={promptModal?.label ?? ""}
        onConfirm={handlePromptConfirm}
        onCancel={() => setPromptModal(null)}
      />

      {/* ── Chat panel — floating vs full-height sidebar toggle ─────────── */}
      <div
        className={`pointer-events-none z-10 w-[360px] max-w-[calc(100vw-2rem)] transition-all duration-300 ease-in-out ${
          isChatMaximized
            ? "fixed inset-y-0 left-0 h-screen"
            : "absolute bottom-4 left-4"
        }`}
        style={
          isChatMaximized
            ? { height: "100vh" }
            : { height: "min(calc(100vh - 2rem), 600px)", minHeight: "400px" }
        }
      >
        <div className="pointer-events-auto h-full w-full">
          <ChatPanel
            ref={chatPanelRef}
            sessionId={sessionId}
            onSessionId={() => {}}
            onGenerated={() => setRefreshSignal((n) => n + 1)}
            referencedElements={referencedElements}
            onClearReference={(id) => setReferencedElements(prev => id ? prev.filter(e => e.id !== id) : [])}
            onTurnEvent={handleTurnEvent}
            onRestoreEvents={handleRestoreEvents}
            isMaximized={isChatMaximized}
            onToggleMaximize={() => setIsChatMaximized((prev) => !prev)}
          />
        </div>
      </div>
    </div>
  );
}
