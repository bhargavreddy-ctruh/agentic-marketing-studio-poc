"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  ApiError,
  ChatTurn,
  IdeationOption,
  NarrativePlan,
  ScenePlan,
  SessionResponse,
  createSession,
  getSession,
  listTurns,
  postTurn,
  cancelTurn,
  updateApprovalMode,
} from "@/lib/api";
import { assetUrl } from "@/lib/http";
import { ReferencedElement } from "@/components/CanvasView";
import { LiveEvent, describeEvent, openEventStream } from "@/lib/events";
import { CanvasElement, getCanvasState } from "@/lib/canvas";

/** `video_stage` values a session's brief can carry while paused at a real pipeline gate
 * (`graph.py`'s `_motion_lead_node`) — used only to pick which proposal detail to render; the
 * gate's actual message/options still come from the real `next_prompt`, same as ideation. */
type GateStage = "narrative_pending" | "scene_pending" | "motion_pending";

interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "error" | "gate";
  text: string;
  options?: IdeationOption[];
  allowFreeText?: boolean;
  gateStage?: GateStage;
  narrativePlan?: NarrativePlan;
  scenePlan?: ScenePlan;
  /** The canvas element this user message referenced when it was sent, if any (2026-09-21, per
   * the user's explicit ask) — real, live-found gap: the "referencing this X" chip only showed
   * while composing, then vanished the instant the message sent (`onClearReference` is one-shot),
   * so chat history had no record of what a follow-up like "make the sky darker" was actually
   * about. Snapshotted onto the message itself, not just referenced by id, so it keeps rendering
   * correctly even if that canvas element is later edited or removed. */
  referencedElements?: ReferencedElement[];
  /** The real accumulated model text streamed live during this turn (`llm_delta` events,
   * core/events.py's accumulator, 2026-09-22) — rendered as a collapsible "Analyzed your request"
   * block right before this message, matching the reference product's own persistent thinking
   * card. Previously this text was shown live while `loading` and then genuinely discarded the
   * instant the turn finished — never part of the chat history at all, gone on refresh. */
  thinking?: string;
  /** Roughly how long this turn took, for the "Analyzed your request Ns" label — real elapsed
   * time for a live turn; recomputed from `created_at` deltas isn't available for restored
   * history (the backend doesn't persist a duration), so restored turns show no number. */
  thinkingSeconds?: number;
}

// A real, live-found bug (2026-09-22): a module-scoped mutable counter is fragile in ways that
// don't show up until something reloads the module underneath already-rendered state — a Next.js
// Fast Refresh (or React 18 Strict Mode's dev-only double-invoke of mount effects) can re-execute
// this module fresh, resetting `nextId` back to 0, while `messages` in React state still holds
// earlier ids like "m8" — the next real message then collides with an old one, producing React's
// "two children with the same key" warning (and the duplicated/omitted rendering that comes with
// it). `crypto.randomUUID()` has no shared mutable state to reset, so this whole class of bug is
// structurally impossible, not just less likely.
function newId(): string {
  return crypto.randomUUID();
}

function truncate(text: string, maxChars: number): string {
  const t = text.trim();
  return t.length > maxChars ? `${t.slice(0, maxChars - 1).trimEnd()}…` : t;
}

/**
 * Turns one real SessionResponse into what the chat should say next — status is a real backend
 * signal, not decoration. `next_prompt` is populated for ideation AND every HITL gate (the same
 * shape, per Architecture.md section 1d); it's genuinely null on "completed" (the generated
 * element itself lives on the canvas, Phase 4b, not in this response) and on some error/pending
 * cases, so those need their own honest fallback text rather than showing nothing.
 */
function describeResponse(
  res: SessionResponse,
  thinking?: string,
  thinkingSeconds?: number,
): ChatMessage {
  const thinkingFields = thinking ? { thinking, thinkingSeconds } : {};
  // Checked before next_prompt: a real backend failure (e.g. a free-tier model returning
  // malformed JSON — a known, documented category of flakiness, not a frontend bug) still
  // populates next_prompt.message with the real error text, but status: "error" is what actually
  // says this turn failed — check status first so it renders as an error, not a normal reply.
  if (res.status === "error") {
    return {
      id: newId(), role: "error",
      text: res.next_prompt?.message || "Something went wrong on that turn.",
      ...thinkingFields,
    };
  }
  // A real pipeline gate (narrative/scene/motion-spend, `graph.py`'s `_motion_lead_node`) is the
  // same next_prompt shape ideation uses, distinguished only by status + `brief.video_stage` —
  // rendered as its own bubble so a real approval checkpoint reads differently from a normal
  // ideation reply, with the actual staged proposal shown, not just the message text.
  if (res.status === "awaiting_approval" && res.next_prompt) {
    const stage = res.brief.video_stage as GateStage | undefined;
    return {
      id: newId(),
      role: "gate",
      text: res.next_prompt.message,
      options: res.next_prompt.options,
      allowFreeText: res.next_prompt.allow_free_text,
      gateStage: stage,
      narrativePlan: res.brief.narrative_plan as NarrativePlan | undefined,
      scenePlan: res.brief.scene_plan as ScenePlan | undefined,
      ...thinkingFields,
    };
  }
  if (res.next_prompt) {
    return {
      id: newId(),
      role: "assistant",
      text: res.next_prompt.message,
      options: res.next_prompt.options,
      allowFreeText: res.next_prompt.allow_free_text,
      ...thinkingFields,
    };
  }
  if (res.status === "completed") {
    return {
      id: newId(),
      role: "assistant",
      text: "Generated — check the canvas behind this chat.",
      ...thinkingFields,
    };
  }
  return { id: newId(), role: "assistant", text: `Status: ${res.status}`, ...thinkingFields };
}

interface ChatPanelProps {
  sessionId: string | null;
  onSessionId: (id: string) => void;
  onGenerated?: () => void;
  referencedElements?: ReferencedElement[];
  onClearReference?: (id?: string) => void;
  /** Every raw event for the turn currently in flight, forwarded up to `page.tsx` — Node Mode
   * (`NodeGraphView`) needs the full real event stream, not just the human-readable narration
   * lines this panel builds for itself. Called once per real event, in order; `page.tsx` resets
   * its own accumulated list on `turn_started` (the one event every turn always emits first). */
  onTurnEvent?: (event: LiveEvent) => void;
  /** The real, persisted run history for the LAST turn only, restored on mount (2026-09-22) —
   * reverted from restoring every past turn (a real, live-found design mistake caught right after
   * shipping it: that made Node Mode a wall of every stale run this session ever had, instead of
   * just the current one). This exists so a refresh mid-generation doesn't lose visibility into
   * whichever run was actually in flight; it is never meant to replay full session history. */
  onRestoreEvents?: (events: LiveEvent[]) => void;
}

/** Imperative handle so a sibling (the canvas's right-click "New Image"/"New Video") can submit a
 * real chat turn without duplicating any of this panel's own state/SSE/error handling — the
 * canvas has no session/turn machinery of its own by design (Rules.md: one file owns each
 * mechanism), so it reuses this panel's real `handleSend` rather than reimplementing a second,
 * parallel way to post a turn. */
export interface ChatPanelHandle {
  sendFreeText: (text: string) => void;
}

function ChatPanel(
  { sessionId, onSessionId, onGenerated, referencedElements, onClearReference, onTurnEvent, onRestoreEvents }: ChatPanelProps,
  ref: React.ForwardedRef<ChatPanelHandle>,
) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [approvalMode, setApprovalMode] = useState<"auto" | "approve">("auto");
  const [changingMode, setChangingMode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [narration, setNarration] = useState<string[]>([]);
  // The real raw model text streaming live DURING the current turn (2026-09-22, per an explicit
  // user ask: "i want it as it generates" — the collapsed post-hoc "Analyzed your request" block
  // alone wasn't enough; they want to watch it being written, not just read a summary afterward).
  // Real React state (not a plain local variable) specifically so it re-renders as it grows —
  // `narration`'s structured status lines ("🎨 illustrator started") stay separate and shown
  // alongside this, since they answer a different question (which STEP is running) than this does
  // (what is the model actually REASONING, live).
  const [liveThinking, setLiveThinking] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  
  const [activeTab, setActiveTab] = useState<"Chat" | "Assets" | "Plan">("Chat");
  const [canvasAssets, setCanvasAssets] = useState<CanvasElement[]>([]);
  const [loadingAssets, setLoadingAssets] = useState(false);

  useEffect(() => {
    if (activeTab === "Assets" && sessionId) {
      setLoadingAssets(true);
      getCanvasState(sessionId)
        .then(state => setCanvasAssets(state.elements))
        .catch(console.error)
        .finally(() => setLoadingAssets(false));
    }
  }, [activeTab, sessionId]);
  // Real, live-found bug (2026-09-23): ANY reply to a still-open prompt — not just "Try again",
  // also a real ideation option pick or a plain free-text answer to a clarifying question — sent
  // an EMPTY referencedElementIds, because the reference chip (`referencedElements` prop) is
  // deliberately cleared right after the turn that first used it. Correct for a genuinely NEW
  // typed message (a stale chip shouldn't leak into later, unrelated turns); wrong for a
  // continuation, which isn't a new request, it's the same one still being resolved. Confirmed
  // widespread via this session's own real chat history: many real turns across a full day
  // (option picks like "Mid Right", free-text replies like "20% discount") all lost their
  // original image reference the same way — backend then fell back to "most recently created
  // element in the whole session", producing edits on the wrong asset entirely. Tracks the ids
  // actually used by the last turn that had a real reference of its own, so `handleSend`/
  // `handlePickOption` can resend the SAME reference for any continuation that doesn't supply a
  // new one (see `isContinuationOfPrompt` below).
  const lastReferencedIdsRef = useRef<string[] | undefined>(undefined);

  // Always show the latest chat content (2026-09-22, per an explicit user ask) — scrolls the
  // message list to the bottom whenever anything new appears: a message, live-streamed thinking,
  // or a narration status line, so the user never has to manually scroll down to see what just
  // arrived.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, liveThinking, narration, loading]);

  /** Opens the real SSE stream for this turn (Phase 4c) and narrates it live — only possible once
   * a sessionId already exists (the backend's own disclosed scope boundary: the very first
   * message can't be watched live since its session_id isn't known until it returns). Also
   * accumulates the REAL raw model text (`llm_delta` events) as it streams — the actual
   * "thinking" content (2026-09-22), separate from the human-readable status lines `narration`
   * already shows; `core/events.py` persists the exact same accumulated text server-side, so this
   * client-side copy is what the message will show immediately, matching what a later refresh
   * would restore. Returns it (plus elapsed seconds) alongside the call's own result — previously
   * this was thrown away the instant the turn finished, never part of the chat history at all. */
  async function withNarration<T>(
    sid: string,
    fn: () => Promise<T>,
  ): Promise<{ result: T; thinking: string; seconds: number }> {
    setNarration([]);
    setLiveThinking("");
    const startedAt = Date.now();
    let thinking = "";
    const close = openEventStream(sid, (event) => {
      const line = describeEvent(event);
      if (line) setNarration((n) => [...n, line]);
      if (event.type === "llm_delta" && typeof event.text === "string") {
        thinking += event.text;
        setLiveThinking(thinking);
      }
      onTurnEvent?.(event);
    });
    try {
      const result = await fn();
      return { result, thinking, seconds: Math.round((Date.now() - startedAt) / 1000) };
    } finally {
      close();
      setNarration([]);
      setLiveThinking("");
    }
  }

  function appendUser(text: string, referencedElements?: ReferencedElement[]) {
    setMessages((m) => [...m, { id: newId(), role: "user", text, referencedElements }]);
  }

  /** Real, live-found bug (2026-09-23), broader than the "Try again" case fixed earlier today:
   * confirmed via this session's own real chat history (many real turns over the day) that ANY
   * continuation of a pending clarification — picking a real ideation option ("Mid Right"), or
   * just typing a free-text answer to a question the backend asked ("20% discount") — silently
   * sent an EMPTY referencedElementIds, not just a "Try again" click. Root cause is the same one
   * `lastReferencedIdsRef` was built for: the reference chip is cleared right after the turn that
   * first used it, so by the time the user replies to whatever the backend asks next, there's
   * nothing left to resend. A continuation isn't a new request, it's the same one still being
   * resolved, so it must keep the same reference unless the user explicitly picked a different one
   * for this reply. Detected by checking whether the message this is replying to still had
   * options/allowFreeText attached (i.e. it was a live, unresolved prompt) BEFORE
   * `stripOptionsFromLastMessage` clears that marker below. */
  function isContinuationOfPrompt(): boolean {
    if (messages.length === 0) return false;
    const last = messages[messages.length - 1];
    return last.options !== undefined || last.allowFreeText !== undefined;
  }

  function stripOptionsFromLastMessage() {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.options !== undefined || last.allowFreeText !== undefined) {
        return [...prev.slice(0, -1), { ...last, options: undefined, allowFreeText: undefined }];
      }
      return prev;
    });
  }

  function appendError(err: unknown) {
    const text = err instanceof ApiError ? `${err.message} (HTTP ${err.status})` : "Network error — is the backend running?";
    setMessages((m) => [...m, { id: newId(), role: "error", text }]);
  }

  async function handleSend(freeTextOverride?: string) {
    const text = freeTextOverride ?? input.trim();
    if (!text || loading) return;
    const wasContinuation = isContinuationOfPrompt();
    setInput("");
    // Collapse the auto-grown textarea back to one row — its height is set imperatively via
    // inline style (`onChange` above), so clearing `input` alone wouldn't reset it.
    if (inputRef.current) inputRef.current.style.height = "auto";
    stripOptionsFromLastMessage();
    appendUser(text, referencedElements && referencedElements.length > 0 ? referencedElements : undefined);
    setLoading(true);
    try {
      // Turn 1 streams exactly like every later turn now: create the (empty) session first so
      // the SSE stream can open against a real id, THEN send the message as a normal turn —
      // fixes live "thinking"/Node Mode being empty for a session's very first message.
      let sid = sessionId;
      if (!sid) {
        const created = await createSession(approvalMode);
        sid = created.id;
        onSessionId(sid);
      }

      // Clear the UI reference immediately so it doesn't linger while generating. A reply to a
      // still-open prompt with no NEW reference of its own reuses the reference the prompt itself
      // was about (see `isContinuationOfPrompt` above); anything else — including a fresh,
      // unrelated message with no reference — genuinely has none, and resets what's "last".
      const freshIds = referencedElements?.map(e => e.id);
      const idsToSend = freshIds && freshIds.length > 0
        ? freshIds
        : wasContinuation ? lastReferencedIdsRef.current : freshIds;
      lastReferencedIdsRef.current = idsToSend;
      onClearReference?.();

      const { result: res, thinking, seconds } = await withNarration(sid, () =>
        postTurn(sid, { freeText: text, referencedElementIds: idsToSend }),
      );
      setMessages((m) => [...m, describeResponse(res, thinking, seconds)]);
      if (res.status === "completed") onGenerated?.();
    } catch (err) {
      appendError(err);
    } finally {
      setLoading(false);
    }
  }

  useImperativeHandle(ref, () => ({
    sendFreeText: (text: string) => {
      void handleSend(text);
    },
  }));

  async function handlePickOption(option: IdeationOption) {
    if (!sessionId || loading) return;
    const wasContinuation = isContinuationOfPrompt();
    stripOptionsFromLastMessage();
    appendUser(option.label, referencedElements && referencedElements.length > 0 ? referencedElements : undefined);
    setLoading(true);
    try {
      // Clear the UI reference immediately so it doesn't linger while generating. Picking ANY
      // option — not just "Try again" — is a reply to a still-open prompt, not a new request, so
      // it reuses that prompt's own reference unless this pick came with a new one of its own
      // (see `isContinuationOfPrompt`/`handleSend` above — same real bug, same fix, both paths).
      const freshIds = referencedElements?.map(e => e.id);
      const idsToSend = freshIds && freshIds.length > 0
        ? freshIds
        : wasContinuation ? lastReferencedIdsRef.current : freshIds;
      lastReferencedIdsRef.current = idsToSend;
      onClearReference?.();

      const { result: res, thinking, seconds } = await withNarration(sessionId, () =>
        postTurn(sessionId, { pickedOptionId: option.id, referencedElementIds: idsToSend }),
      );
      setMessages((m) => [...m, describeResponse(res, thinking, seconds)]);
      if (res.status === "completed") onGenerated?.();
    } catch (err) {
      appendError(err);
    } finally {
      setLoading(false);
    }
  }

  function handleOther() {
    // "Other" doesn't send anything itself — it just points the user at the one text input that
    // already exists, rather than duplicating a second free-text field inline per message.
    inputRef.current?.focus();
    inputRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function loadHistory(isCancelled?: () => boolean) {
    if (!sessionId) return;
    try {
      const [session, turns] = await Promise.all([getSession(sessionId), listTurns(sessionId)]);
      if (isCancelled?.()) return;
      
      if (session.approval_mode === "auto" || session.approval_mode === "approve") {
        setApprovalMode(session.approval_mode);
      }
      const restored: ChatMessage[] = turns.flatMap((turn: ChatTurn) => [
        { id: newId(), role: "user" as const, text: turn.user_text, referencedElements: turn.referenced_elements },
        { id: newId(), role: "assistant" as const, text: turn.assistant_text ?? "", thinking: turn.thinking_text ?? undefined },
      ]);
      
      const lastTurnForNode = turns[turns.length - 1];
      if (lastTurnForNode) onRestoreEvents?.(lastTurnForNode.events as LiveEvent[]);
      
      if (session.status === "generating") {
        restored.push({ id: newId(), role: "assistant", text: "Reconnecting — a generation is still in progress…" });
        setMessages(restored);
        await pollUntilResolved(sessionId, () => isCancelled?.() ?? false);
        return;
      }
      
      if (turns.length === 0) {
        setMessages([]);
        return;
      }
      restored[restored.length - 1] = describeResponse(session, turns[turns.length - 1].thinking_text ?? undefined);
      setMessages(restored);
    } catch (err) {
      appendError(err);
    }
  }

  async function handleRefresh() {
    await loadHistory();
  }

  useEffect(() => {
    let cancelled = false;
    loadHistory(() => cancelled);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  /** Polls the session every 2s while a turn genuinely is still running server-side (2026-09-22 —
   * see the mount effect above for why this exists at all). Stops on unmount (`isCancelled`), or
   * once `status` moves past "generating" — then reconciles the SAME way a live turn's own
   * completion already does: refetch the real persisted turn record (so thinking/assistant text
   * match exactly what a fresh restore would show later), tell the canvas to refresh
   * (`onGenerated`), and tell Node Mode the turn is over (`onTurnEvent`) since no live SSE events
   * for this turn were ever seen by this tab. */
  async function pollUntilResolved(sid: string, isCancelled: () => boolean) {
    setLoading(true);
    setNarration([]);
    setLiveThinking("");
    let thinking = "";
    const close = openEventStream(sid, (event) => {
      const line = describeEvent(event);
      if (line) setNarration((n) => [...n, line]);
      if (event.type === "llm_delta" && typeof event.text === "string") {
        thinking += event.text;
        setLiveThinking(thinking);
      }
      onTurnEvent?.(event);
    });

    try {
      while (!isCancelled()) {
        await new Promise((r) => setTimeout(r, 2000));
        if (isCancelled()) return;
        let session: SessionResponse;
        try {
          session = await getSession(sid);
        } catch {
          continue; // a transient network hiccup — keep polling, don't give up on one failed check
        }
        if (session.status === "generating") continue;
        const turns = await listTurns(sid).catch(() => [] as ChatTurn[]);
        const lastTurn = turns[turns.length - 1];
        setMessages((m) => [
          ...m.slice(0, -1), // drop the "Reconnecting…" placeholder
          describeResponse(session, lastTurn?.thinking_text ?? undefined),
        ]);
        if (session.status === "completed") onGenerated?.();
        // The turn that just resolved has its own real persisted events now too — restore just
        // THIS turn's (not the whole session's), matching the mount effect's own last-turn-only fix.
        if (lastTurn) onRestoreEvents?.(lastTurn.events as LiveEvent[]);
        onTurnEvent?.({ type: "turn_completed" } as LiveEvent);
        return;
      }
    } finally {
      close();
      setNarration([]);
      setLiveThinking("");
      setLoading(false);
    }
  }

  return (
    <div className="flex h-full w-full flex-col rounded-2xl border border-surface-700/50 bg-[#1e1e1e] p-4 shadow-2xl overflow-hidden font-sans">
      <header className="mb-3 flex flex-col gap-4 border-b border-surface-700/50 pb-4">
        <div className="flex items-center justify-between text-sm font-medium text-surface-400">
          <div className="flex gap-2">
            <button 
              onClick={() => setActiveTab("Chat")}
              className={`rounded-full px-4 py-1.5 transition-colors ${activeTab === "Chat" ? "bg-surface-800/80 text-white shadow-sm ring-1 ring-surface-700" : "hover:text-white"}`}>
              Chat
            </button>
            <button 
              onClick={() => setActiveTab("Assets")}
              className={`rounded-full px-4 py-1.5 transition-colors ${activeTab === "Assets" ? "bg-surface-800/80 text-white shadow-sm ring-1 ring-surface-700" : "hover:text-white"}`}>
              Assets
            </button>
            <button 
              onClick={() => setActiveTab("Plan")}
              className={`rounded-full px-4 py-1.5 transition-colors ${activeTab === "Plan" ? "bg-surface-800/80 text-white shadow-sm ring-1 ring-surface-700" : "hover:text-white"}`}>
              Plan
            </button>
          </div>
          <button className="text-surface-400 hover:text-white p-1">
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>
        
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 cursor-pointer hover:opacity-80 transition-opacity">
            <h1 className="text-base font-semibold text-white sm:text-lg">Social Media Video Ads</h1>
            <svg className="h-4 w-4 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
          {sessionId && (
            <div className="flex items-center gap-3 text-xs text-surface-500">
              <span className="font-mono">ID: {sessionId.slice(0, 8)}</span>
              <button onClick={handleRefresh} className="hover:text-white transition-colors">
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
              </button>
            </div>
          )}
        </div>
      </header>

      {activeTab === "Chat" && (
        <div className="flex-1 space-y-4 overflow-y-auto pr-1 scrollbar-thin scrollbar-track-transparent scrollbar-thumb-surface-700">
          {messages.length === 0 && (
            <div className="flex h-full items-center justify-center">
            <p className="text-sm text-surface-500 text-center max-w-xs">
              Describe what you want to build or generate. I can help you plan, write, and create visual assets.
            </p>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className="animate-in fade-in slide-in-from-bottom-2 duration-300">
            {/* Real, persisted "thinking" (2026-09-22) — the actual raw model text streamed live
             * during this turn, collapsible like the reference product's own "Analyzed your
             * request" card, rendered right before the response it led to. Previously this text
             * was shown only while the turn was in flight, then discarded the instant it finished
             * — never part of the chat history, gone on refresh. */}
            {m.thinking && (
              <div className="mb-2 pl-4 border-l-2 border-surface-700/50">
                <details className="text-xs group">
                  <summary className="cursor-pointer select-none font-medium text-surface-500 hover:text-surface-400 flex items-center gap-1.5 transition-colors">
                    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                    Analyzed your request{m.thinkingSeconds != null ? ` ${m.thinkingSeconds}s` : ""}
                  </summary>
                  <p className="mt-2 whitespace-pre-wrap italic text-surface-500 pr-4">{m.thinking}</p>
                </details>
              </div>
            )}
            <div className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div
              className={
                "max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed " +
                (m.role === "user"
                  ? "bg-surface-800 text-surface-100 shadow-md"
                  : m.role === "error"
                    ? "bg-red-900/20 text-red-200 border border-red-900/50"
                    : m.role === "gate"
                      ? m.gateStage === "motion_pending"
                        ? "bg-red-900/10 text-surface-200 border border-red-900/30"
                        : "bg-surface-800/50 text-surface-200 border border-surface-700/50"
                      : "text-surface-200")
              }
            >
              {m.role === "gate" && (
                <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-surface-400">
                  <span className="flex h-4 w-4 items-center justify-center rounded-full bg-surface-700 text-[10px]">⏸</span> 
                  {m.gateStage === "motion_pending" ? "Spend approval needed" : "Approval needed"}
                </p>
              )}
              {m.role === "user" && m.referencedElements && m.referencedElements.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-2">
                  {m.referencedElements.map((el) => (
                    <div key={el.id} className="flex max-w-[80%] items-center gap-2 overflow-hidden rounded-lg bg-surface-900/50 p-1 pl-2 border border-surface-700/50">
                      <span className="truncate text-xs text-surface-300" title={el.description ?? undefined}>
                        re: {el.kind}
                        {el.description && <> — {truncate(el.description, 40)}</>}
                      </span>
                      {el.kind === "video" ? (
                        <video src={el.url} className="h-8 w-8 shrink-0 rounded-md object-cover" />
                      ) : el.kind === "audio" ? (
                        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-surface-800">
                          <svg className="h-4 w-4 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072M17.657 6.343a8 8 0 010 11.314M9 10l-3-3m0 0l3-3m-3 3h12" />
                          </svg>
                        </div>
                      ) : el.kind === "text" ? (
                        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-surface-800">
                          <svg className="h-4 w-4 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h7" />
                          </svg>
                        </div>
                      ) : (
                        <img src={el.url} alt="" className="h-8 w-8 shrink-0 rounded-md object-cover" />
                      )}
                    </div>
                  ))}
                </div>
              )}
              <div className="whitespace-pre-wrap">{m.text}</div>
              
              {/* Asset plan visualizers */}
              {m.role === "gate" && m.gateStage === "narrative_pending" && m.narrativePlan && (
                <div className="mt-3 rounded-xl border border-surface-700/50 bg-surface-900/50 p-3 text-xs shadow-inner">
                  <p className="font-medium text-surface-200 mb-1">Shots</p>
                  <ul className="ml-4 space-y-1 list-disc text-surface-400">
                    {m.narrativePlan.shots.map((shot, i) => (
                      <li key={i}>{shot}</li>
                    ))}
                  </ul>
                  <p className="mt-2 text-surface-400">
                    <span className="font-medium text-surface-300">Story:</span> {m.narrativePlan.overall_story}
                  </p>
                </div>
              )}
              {m.role === "gate" && m.gateStage === "scene_pending" && m.scenePlan && (
                <div className="mt-3 flex gap-3 rounded-xl border border-surface-700/50 bg-surface-900/50 p-3 text-xs shadow-inner">
                  {m.scenePlan.scene_image_storage_ref && (
                    // eslint-disable-next-line @next/next/no-img-element -- a dynamic, backend-served asset thumbnail
                    <img
                      src={assetUrl(m.scenePlan.scene_image_storage_ref)}
                      alt="Scene starting frame"
                      className="h-20 w-20 shrink-0 rounded-lg object-cover border border-surface-700"
                    />
                  )}
                  <div className="text-surface-400 space-y-1">
                    <p>
                      <span className="font-medium text-surface-300">Environment:</span>{" "}
                      {m.scenePlan.environment_description}
                    </p>
                    <p>
                      <span className="font-medium text-surface-300">Lighting:</span> {m.scenePlan.lighting_description}
                    </p>
                    {m.scenePlan.prop_description && (
                      <p>
                        <span className="font-medium text-surface-300">Props:</span> {m.scenePlan.prop_description}
                      </p>
                    )}
                  </div>
                </div>
              )}
              {m.role === "gate" && m.gateStage === "motion_pending" && (
                <div className="mt-3 rounded-xl border border-red-900/30 bg-red-900/10 p-3 text-xs text-surface-400 shadow-inner">
                  {m.narrativePlan && (
                    <p className="mb-1">
                      <span className="font-medium text-surface-300">Shot:</span> {m.narrativePlan.shots[0]}
                    </p>
                  )}
                  {m.scenePlan && (
                    <p className="mb-2">
                      <span className="font-medium text-surface-300">Scene:</span> {m.scenePlan.environment_description}
                    </p>
                  )}
                  <p className="font-medium text-red-400 flex items-center gap-1.5">
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    This will call a paid provider (Replicate).
                  </p>
                </div>
              )}
              {m.options && m.options.length > 0 && (
                <div className="mt-3 flex flex-col gap-2">
                  {m.options.map((opt, i) => (
                    <button
                      key={opt.id}
                      onClick={() => handlePickOption(opt)}
                      disabled={loading}
                      className={
                        "flex items-center justify-between rounded-xl border px-4 py-2.5 text-left text-sm disabled:opacity-50 transition-all hover:scale-[1.01] " +
                        (m.gateStage === "motion_pending" && opt.id === "approve"
                          ? "border-red-900/50 bg-red-900/20 hover:bg-red-900/40 text-red-200"
                          : "border-surface-700/50 bg-surface-800/80 hover:bg-surface-700 text-surface-200")
                      }
                    >
                      <span className="flex items-center gap-3">
                        {opt.id === "approve" ? (
                          <svg className="w-5 h-5 text-green-500/80" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                          </svg>
                        ) : opt.id === "reject" ? (
                          <svg className="w-5 h-5 text-red-500/80" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
                          </svg>
                        ) : (
                          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-surface-700/50 text-xs font-medium text-surface-400">
                            {i + 1}
                          </span>
                        )}
                        <span>
                          <span className="block font-medium">{opt.label}</span>
                          {opt.description && <span className="block text-xs text-surface-500 mt-0.5">{opt.description}</span>}
                        </span>
                      </span>
                      <svg className="w-4 h-4 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                  ))}
                  {m.allowFreeText !== false && (
                    <button
                      onClick={handleOther}
                      disabled={loading}
                      className="flex items-center gap-3 rounded-xl border border-dashed border-surface-700 bg-transparent px-4 py-2.5 text-left text-sm hover:bg-surface-800/50 disabled:opacity-50 transition-all text-surface-400 hover:text-surface-300"
                    >
                      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-surface-800 text-xs font-medium">
                        {m.options.length + 1}
                      </span>
                      <span>
                        <span className="block font-medium">Type your own reply below</span>
                      </span>
                    </button>
                  )}
                </div>
              )}
            </div>
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex items-center gap-3 text-xs text-surface-500 pl-2 animate-in fade-in duration-300">
            <svg className="w-4 h-4 animate-spin text-surface-600" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <div className="flex flex-col">
              {narration.length === 0 ? (
                <span className="italic">Imagining...</span>
              ) : (
                <span className="italic text-surface-400">{narration[narration.length - 1]}</span>
              )}
            </div>
          </div>
        )}
        {/* A real, live-found gap (2026-09-22, per an explicit user ask: "fix the chat to show
         * latest chat everytime") — nothing kept the message list scrolled to the bottom as new
         * messages arrived; only the live-thinking paragraph above had its own one-off
         * `scrollIntoView`. This sentinel + effect covers every real case that adds content
         * (a new message, streaming thinking, narration lines) in one place. */}
        <div ref={bottomRef} />
        </div>
      )}

      {activeTab === "Assets" && (
        <div className="flex-1 overflow-y-auto pr-1 scrollbar-thin scrollbar-track-transparent scrollbar-thumb-surface-700 p-1">
          {loadingAssets ? (
            <div className="flex h-full items-center justify-center text-sm text-surface-500">
              <svg className="w-5 h-5 animate-spin mr-2" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              Loading assets...
            </div>
          ) : canvasAssets.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-surface-500 text-center px-4">
              No assets generated yet. Start a chat to generate images, videos, or audio.
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 pb-2">
              {canvasAssets.map(asset => (
                <div key={asset.id} className="relative rounded-xl border border-surface-700/50 bg-surface-800/50 overflow-hidden group aspect-square flex flex-col shadow-sm transition-all hover:border-brand-500/50">
                  <div className="flex-1 bg-surface-900/50 flex items-center justify-center overflow-hidden relative">
                    {asset.storage_ref ? (
                      asset.element_type === "video" ? (
                        <video src={assetUrl(asset.storage_ref)} className="w-full h-full object-cover" />
                      ) : asset.element_type === "audio" ? (
                        <div className="w-full h-full flex flex-col items-center justify-center text-surface-400 gap-2">
                          <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c-1.105 0-2 .895-2 2s.895 2 2 2 2-.895 2-2-.895-2-2-2zM21 16c-1.105 0-2 .895-2 2s.895 2 2 2 2-.895 2-2-.895-2-2-2z" /></svg>
                        </div>
                      ) : (
                        <img src={assetUrl(asset.storage_ref)} className="w-full h-full object-cover" />
                      )
                    ) : asset.element_type === "text" ? (
                      <div className="w-full h-full p-3 text-xs text-surface-300 overflow-y-auto bg-surface-900">{asset.text_content}</div>
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-surface-500 text-xs">Processing...</div>
                    )}
                    
                    {/* Hover overlay */}
                    <div className="absolute inset-0 bg-black/70 opacity-0 group-hover:opacity-100 transition-opacity flex flex-col justify-end p-3 backdrop-blur-[2px]">
                      <span className="text-xs text-white font-medium line-clamp-2">{asset.description || asset.element_type}</span>
                      <div className="flex items-center justify-between mt-2 text-[10px] text-surface-400">
                        <span className="px-1.5 py-0.5 rounded bg-surface-800/80">v{asset.version}</span>
                        <span>{new Date(asset.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</span>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === "Plan" && (
        <div className="flex-1 flex flex-col items-center justify-center text-center p-6 space-y-3 opacity-60">
          <svg className="w-10 h-10 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
          </svg>
          <div>
            <p className="text-sm font-medium text-surface-200">Plan View</p>
            <p className="text-xs text-surface-400 mt-1">We will think about it later.</p>
          </div>
        </div>
      )}

      {activeTab === "Chat" && referencedElements && referencedElements.length > 0 && (
        <div className="mt-4 flex flex-col gap-2 rounded-xl border border-surface-700/80 bg-surface-800/50 p-3 text-xs backdrop-blur">
          <div className="flex items-center justify-between font-medium text-surface-300">
            <span className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
              Referencing {referencedElements.length} element{referencedElements.length > 1 ? "s" : ""}
            </span>
            <button onClick={() => onClearReference?.()} className="text-surface-500 hover:text-surface-300 transition-colors">
              Clear all
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {referencedElements.map((el) => (
              <div key={el.id} className="flex items-center gap-2 rounded-lg bg-surface-900/80 p-1.5 border border-surface-700/50">
                {el.kind === "video" ? (
                  <video src={el.url} className="h-8 w-8 rounded-md object-cover" />
                ) : el.kind === "audio" ? (
                  <span className="flex h-8 w-8 items-center justify-center rounded-md bg-surface-800 text-sm">🔊</span>
                ) : el.kind === "text" ? (
                  <span className="flex h-8 w-8 items-center justify-center rounded-md bg-surface-800 text-sm">📄</span>
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element -- a dynamic, backend-served asset thumbnail
                  <img src={el.url} alt="" className="h-8 w-8 rounded-md object-cover" />
                )}
                <span className="text-surface-300 pr-1" title={el.description ?? undefined}>
                  {el.kind}
                  {el.description && <> — <span className="text-surface-500">{truncate(el.description, 30)}</span></>}
                </span>
                <button
                  onClick={() => onClearReference?.(el.id)}
                  className="ml-auto text-surface-500 hover:text-surface-300 px-1 rounded-full hover:bg-surface-800"
                  aria-label={`Remove reference to ${el.kind}`}
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <form
        className="mt-4 flex flex-col gap-2 rounded-2xl border border-surface-700/60 bg-surface-800/40 p-2 shadow-inner transition-colors focus-within:border-surface-600 focus-within:bg-surface-800/60"
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
      >
        <textarea
          ref={inputRef}
          rows={1}
          className="max-h-32 w-full resize-none bg-transparent px-3 py-2 text-sm text-white placeholder-surface-500 focus:outline-none scrollbar-thin scrollbar-track-transparent scrollbar-thumb-surface-700"
          placeholder={sessionId ? "What do you want to do?" : "What do you want to create?"}
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            const el = e.target;
            el.style.height = "auto";
            el.style.height = `${Math.min(el.scrollHeight, 128)}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          disabled={loading}
        />
        <div className="flex items-center justify-between px-2 pb-1">
          <div className="relative group flex items-center">
            {/* The approve/auto mode dropdown replaces the old mode selector */}
            <select
              className="appearance-none bg-transparent py-1 pl-6 pr-4 text-xs font-medium text-surface-400 hover:text-surface-200 focus:outline-none cursor-pointer transition-colors"
              value={approvalMode}
              disabled={loading || changingMode || !sessionId}
              onChange={async (e) => {
                const next = e.target.value as "auto" | "approve";
                const prev = approvalMode;
                setApprovalMode(next);
                if (sessionId) {
                  setChangingMode(true);
                  try {
                    await updateApprovalMode(sessionId, next);
                  } catch (err) {
                    setApprovalMode(prev);
                    appendError(err);
                  } finally {
                    setChangingMode(false);
                  }
                }
              }}
            >
              <option value="auto" className="bg-surface-900 text-surface-200">Auto</option>
              <option value="approve" className="bg-surface-900 text-surface-200">Approve</option>
            </select>
            <div className="pointer-events-none absolute left-1 flex items-center text-surface-500 group-hover:text-surface-300">
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
              </svg>
            </div>
            <div className="pointer-events-none absolute right-0 flex items-center text-surface-500 group-hover:text-surface-300">
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 9l4-4 4 4m0 6l-4 4-4-4" />
              </svg>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button type="button" className="text-surface-500 hover:text-surface-300 transition-colors" title="Voice Input">
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
              </svg>
            </button>
            {loading ? (
              <button
                type="button"
                onClick={async () => {
                  if (!sessionId) return;
                  try {
                    await cancelTurn(sessionId);
                  } catch (e) {
                    console.error("Failed to cancel", e);
                    appendError(e);
                    setLoading(false);
                  }
                }}
                className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-200 text-surface-900 transition-transform hover:scale-105 shadow-sm"
              >
                <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="flex h-8 w-8 items-center justify-center rounded-full bg-white text-black transition-transform disabled:opacity-30 disabled:hover:scale-100 hover:scale-105 shadow-sm"
              >
                <svg className="h-4 w-4 translate-x-[1px]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14M12 5l7 7-7 7" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
}

export default forwardRef(ChatPanel);
