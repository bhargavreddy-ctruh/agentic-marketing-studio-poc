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
} from "@/lib/api";
import { assetUrl } from "@/lib/http";
import { ReferencedElement } from "@/components/CanvasView";
import { LiveEvent, describeEvent, openEventStream } from "@/lib/events";

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

  async function handleRefresh() {
    if (!sessionId) return;
    try {
      const res = await getSession(sessionId);
      setMessages((m) => [...m, describeResponse(res)]);
    } catch (err) {
      appendError(err);
    }
  }

  // Real chat-history persistence (2026-09-22, per an explicit user ask): a session id arrives as
  // a prop from the very first render (the studio page reads it from the URL), so on mount this
  // restores the REAL, FULL turn-by-turn conversation — every prior user message, the real
  // "thinking" text streamed live during each turn, and the real response — not just a single
  // "here's where things currently stand" placeholder like the earlier version of this fix. Every
  // restored turn except the LAST renders as plain (frozen) history; the last one reuses
  // `describeResponse` against the real CURRENT session state so its options/gate are still
  // genuinely interactive, not a dead copy of stale option ids.
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    (async () => {
      try {
        const [session, turns] = await Promise.all([getSession(sessionId), listTurns(sessionId)]);
        if (session.approval_mode === "auto" || session.approval_mode === "approve") {
          setApprovalMode(session.approval_mode);
        }
        const restored: ChatMessage[] = turns.flatMap((turn: ChatTurn) => [
          { id: newId(), role: "user" as const, text: turn.user_text, referencedElements: turn.referenced_elements },
          { id: newId(), role: "assistant" as const, text: turn.assistant_text ?? "", thinking: turn.thinking_text ?? undefined },
        ]);
        // Real, persisted Node Mode history for the LAST turn only (2026-09-22) — a real,
        // live-found design mistake caught right after shipping: restoring and accumulating EVERY
        // past turn made Node Mode a wall of dozens of stale cards instead of the simple "just the
        // current run" view it always was. The actual gap worth closing is narrower: don't lose
        // visibility into the one turn that might have genuinely been interrupted by the refresh.
        const lastTurnForNode = turns[turns.length - 1];
        if (lastTurnForNode) onRestoreEvents?.(lastTurnForNode.events as LiveEvent[]);
        // A real, live-found gap (2026-09-22): a reload while a turn was genuinely still running
        // server-side used to show nothing at all — `session.status` (a POST-turn resting state
        // like "completed"/"ideating") was checked with no notion that "generating" (set the
        // instant a turn starts, `session_service.py`'s `_run_turn`) even exists, so the restored
        // view either stopped at whatever the LAST finished turn looked like, or — for a
        // session's very first turn — showed nothing, even though the backend keeps the pipeline
        // running to completion regardless of the client having disconnected. Poll until it
        // resolves, exactly like a live turn would, instead of leaving the user staring at a dead
        // page for a generation that's already progressing or done.
        if (session.status === "generating") {
          restored.push({ id: newId(), role: "assistant", text: "Reconnecting — a generation is still in progress…" });
          setMessages(restored);
          await pollUntilResolved(sessionId, () => cancelled);
          return;
        }
        // A brand-new, never-touched workflow (just created, nothing sent yet) has no real
        // history — leave `messages` empty so the normal "describe what you want" empty state
        // shows, rather than an odd empty restore.
        if (turns.length === 0) return;
        restored[restored.length - 1] = describeResponse(session, turns[turns.length - 1].thinking_text ?? undefined);
        setMessages(restored);
      } catch (err) {
        appendError(err);
      }
    })();
    // Only ever on mount — this session id is fixed for this page's whole lifetime (the studio
    // page is keyed by the URL param), never reassigned to a different real session in place.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => {
      cancelled = true;
    };
  }, []);

  /** Polls the session every 2s while a turn genuinely is still running server-side (2026-09-22 —
   * see the mount effect above for why this exists at all). Stops on unmount (`isCancelled`), or
   * once `status` moves past "generating" — then reconciles the SAME way a live turn's own
   * completion already does: refetch the real persisted turn record (so thinking/assistant text
   * match exactly what a fresh restore would show later), tell the canvas to refresh
   * (`onGenerated`), and tell Node Mode the turn is over (`onTurnEvent`) since no live SSE events
   * for this turn were ever seen by this tab. */
  async function pollUntilResolved(sid: string, isCancelled: () => boolean) {
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
  }

  return (
    <div className="flex h-full w-full flex-col rounded-2xl border border-surface-700/50 bg-surface-900/60 p-4 shadow-2xl backdrop-blur-xl">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-surface-700/50 pb-3">
        <h1 className="whitespace-nowrap text-base font-semibold sm:text-lg">Agentic Marketing Studio</h1>
        {!sessionId ? (
          <label className="flex items-center gap-2 text-sm text-neutral-600">
            Mode
            <select
              className="rounded border border-neutral-300 px-2 py-1"
              value={approvalMode}
              onChange={(e) => setApprovalMode(e.target.value as "auto" | "approve")}
            >
              <option value="auto">Auto (no pauses)</option>
              <option value="approve">Approve (real HITL gates)</option>
            </select>
          </label>
        ) : (
          <div className="text-xs text-neutral-500">
            Session <code>{sessionId.slice(0, 8)}</code> · {approvalMode}
            <button onClick={handleRefresh} className="ml-2 underline">
              refresh
            </button>
          </div>
        )}
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto pr-1">
        {messages.length === 0 && (
          <p className="text-sm text-surface-400">
            Describe what you want — e.g. &ldquo;a hero shot for a red running sneaker&rdquo;.
          </p>
        )}
        {messages.map((m) => (
          <div key={m.id}>
            {/* Real, persisted "thinking" (2026-09-22) — the actual raw model text streamed live
             * during this turn, collapsible like the reference product's own "Analyzed your
             * request" card, rendered right before the response it led to. Previously this text
             * was shown only while the turn was in flight, then discarded the instant it finished
             * — never part of the chat history, gone on refresh. */}
            {m.thinking && (
              <details className="mb-1.5 rounded-xl border border-surface-700/50 bg-surface-800/40 px-3 py-2 text-xs">
                <summary className="cursor-pointer select-none font-medium text-surface-400">
                  Analyzed your request{m.thinkingSeconds != null ? ` ${m.thinkingSeconds}s` : ""}
                </summary>
                <p className="mt-1.5 whitespace-pre-wrap italic text-surface-400">{m.thinking}</p>
              </details>
            )}
            <div className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div
              className={
                "max-w-[80%] rounded-2xl px-4 py-2 text-sm " +
                (m.role === "user"
                  ? "bg-gradient-to-br from-brand-500 to-brand-700 text-white shadow-lg"
                  : m.role === "error"
                    ? "bg-red-900/50 text-red-200 border border-red-700/50"
                    : m.role === "gate"
                      ? m.gateStage === "motion_pending"
                        ? "bg-red-900/30 text-surface-100 shadow-sm ring-1 ring-red-500/50"
                        : "bg-brand-900/30 text-surface-100 shadow-sm ring-1 ring-brand-500/50"
                      : "bg-surface-800/80 text-surface-100 shadow-sm ring-1 ring-surface-700/50")
              }
            >
              {m.role === "gate" && (
                <p className="mb-1 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-amber-800">
                  ⏸ {m.gateStage === "motion_pending" ? "Spend approval needed" : "Approval needed"}
                </p>
              )}
              {m.role === "user" && m.referencedElements && m.referencedElements.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-2">
                  {m.referencedElements.map((el) => (
                    <div key={el.id} className="flex max-w-[80%] items-center gap-2 overflow-hidden rounded bg-neutral-800/50 p-1 pl-2">
                      <span className="truncate text-xs text-neutral-300" title={el.description ?? undefined}>
                        re: {el.kind}
                        {el.description && <> — {truncate(el.description, 40)}</>}
                      </span>
                      {el.kind === "video" ? (
                        <video src={el.url} className="h-10 w-10 shrink-0 rounded object-cover" />
                      ) : el.kind === "audio" ? (
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded bg-neutral-700">
                          <svg className="h-5 w-5 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072M17.657 6.343a8 8 0 010 11.314M9 10l-3-3m0 0l3-3m-3 3h12" />
                          </svg>
                        </div>
                      ) : el.kind === "text" ? (
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded bg-neutral-700">
                          <svg className="h-5 w-5 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h7" />
                          </svg>
                        </div>
                      ) : (
                        <img src={el.url} alt="" className="h-10 w-10 shrink-0 rounded object-cover" />
                      )}
                    </div>
                  ))}
                </div>
              )}
              <p className="whitespace-pre-wrap">{m.text}</p>
              {m.role === "gate" && m.gateStage === "narrative_pending" && m.narrativePlan && (
                <div className="mt-2 rounded-lg bg-white/70 p-2 text-xs">
                  <p className="font-medium text-neutral-700">Shots</p>
                  <ul className="ml-4 list-disc text-neutral-600">
                    {m.narrativePlan.shots.map((shot, i) => (
                      <li key={i}>{shot}</li>
                    ))}
                  </ul>
                  <p className="mt-1 text-neutral-600">
                    <span className="font-medium text-neutral-700">Story:</span> {m.narrativePlan.overall_story}
                  </p>
                </div>
              )}
              {m.role === "gate" && m.gateStage === "scene_pending" && m.scenePlan && (
                <div className="mt-2 flex gap-2 rounded-lg bg-white/70 p-2 text-xs">
                  {m.scenePlan.scene_image_storage_ref && (
                    // eslint-disable-next-line @next/next/no-img-element -- a dynamic, backend-served asset thumbnail
                    <img
                      src={assetUrl(m.scenePlan.scene_image_storage_ref)}
                      alt="Scene starting frame"
                      className="h-16 w-16 shrink-0 rounded object-cover"
                    />
                  )}
                  <div className="text-neutral-600">
                    <p>
                      <span className="font-medium text-neutral-700">Environment:</span>{" "}
                      {m.scenePlan.environment_description}
                    </p>
                    <p>
                      <span className="font-medium text-neutral-700">Lighting:</span> {m.scenePlan.lighting_description}
                    </p>
                    {m.scenePlan.prop_description && (
                      <p>
                        <span className="font-medium text-neutral-700">Props:</span> {m.scenePlan.prop_description}
                      </p>
                    )}
                  </div>
                </div>
              )}
              {m.role === "gate" && m.gateStage === "motion_pending" && (
                <div className="mt-2 rounded-lg bg-white/70 p-2 text-xs text-neutral-600">
                  {m.narrativePlan && (
                    <p>
                      <span className="font-medium text-neutral-700">Shot:</span> {m.narrativePlan.shots[0]}
                    </p>
                  )}
                  {m.scenePlan && (
                    <p>
                      <span className="font-medium text-neutral-700">Scene:</span> {m.scenePlan.environment_description}
                    </p>
                  )}
                  <p className="mt-1 font-semibold text-red-700">💸 This will call a paid provider (Replicate).</p>
                </div>
              )}
              {m.options && m.options.length > 0 && (
                <div className="mt-2 flex flex-col gap-2">
                  {m.options.map((opt, i) => (
                    <button
                      key={opt.id}
                      onClick={() => handlePickOption(opt)}
                      disabled={loading}
                      className={
                        "flex items-start gap-2 rounded-lg border px-3 py-2 text-left text-sm disabled:opacity-50 transition-all hover:-translate-y-0.5 " +
                        (m.gateStage === "motion_pending" && opt.id === "approve"
                          ? "border-red-500/50 bg-red-900/30 hover:bg-red-800/40 text-red-100"
                          : "border-surface-700/50 bg-surface-800/50 hover:bg-surface-700/60 text-surface-200")
                      }
                    >
                      <span className="font-semibold text-brand-400">{i + 1}.</span>
                      <span>
                        <span className="block font-medium">{opt.label}</span>
                        <span className="block text-xs text-surface-400">{opt.description}</span>
                      </span>
                    </button>
                  ))}
                  {m.allowFreeText !== false && (
                    <button
                      onClick={handleOther}
                      disabled={loading}
                      className="flex items-start gap-2 rounded-lg border border-dashed border-surface-600/50 bg-surface-800/30 px-3 py-2 text-left text-sm hover:bg-surface-700/50 disabled:opacity-50 transition-all hover:-translate-y-0.5 text-surface-300"
                    >
                      <span className="font-semibold text-neutral-400">{m.options.length + 1}.</span>
                      <span>
                        <span className="block font-medium">Other</span>
                        <span className="block text-xs text-neutral-500">Type your own reply below</span>
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
          <div className="space-y-0.5 text-xs text-neutral-500">
            {narration.length === 0 ? (
              <p className="text-neutral-400">Working…</p>
            ) : (
              narration.map((line, i) => <p key={i}>{line}</p>)
            )}
            {/* The real raw model text, live, AS it streams in (2026-09-22) — not just the
             * structured status lines above, and not only visible after the turn finishes as a
             * collapsed summary. Auto-scrolls into view as it grows since it's the newest,
             * most-likely-to-keep-changing content on screen. */}
            {liveThinking && (
              <p className="mt-1 whitespace-pre-wrap italic text-neutral-400">{liveThinking}</p>
            )}
          </div>
        )}
        {/* A real, live-found gap (2026-09-22, per an explicit user ask: "fix the chat to show
         * latest chat everytime") — nothing kept the message list scrolled to the bottom as new
         * messages arrived; only the live-thinking paragraph above had its own one-off
         * `scrollIntoView`. This sentinel + effect covers every real case that adds content
         * (a new message, streaming thinking, narration lines) in one place. */}
        <div ref={bottomRef} />
      </div>

      {referencedElements && referencedElements.length > 0 && (
        <div className="mt-3 flex flex-col gap-1.5 rounded-lg border border-blue-300 bg-blue-50 p-2.5 text-xs">
          <div className="flex items-center justify-between font-medium text-blue-800">
            <span>Referencing {referencedElements.length} element{referencedElements.length > 1 ? "s" : ""} for your next message:</span>
            <button onClick={() => onClearReference?.()} className="text-blue-600 hover:text-blue-900 underline">
              Clear all
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {referencedElements.map((el) => (
              <div key={el.id} className="flex items-center gap-1.5 rounded-lg bg-white p-1.5 shadow-sm border border-blue-200">
                {el.kind === "video" ? (
                  <video src={el.url} className="h-8 w-8 rounded object-cover" />
                ) : el.kind === "audio" ? (
                  <span className="flex h-8 w-8 items-center justify-center rounded bg-blue-100 text-sm">🔊</span>
                ) : el.kind === "text" ? (
                  <span className="flex h-8 w-8 items-center justify-center rounded bg-blue-100 text-sm">📄</span>
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element -- a dynamic, backend-served asset thumbnail
                  <img src={el.url} alt="" className="h-8 w-8 rounded object-cover" />
                )}
                <span className="text-blue-800" title={el.description ?? undefined}>
                  {el.kind}
                  {el.description && <> — {truncate(el.description, 30)}</>}
                </span>
                <button
                  onClick={() => onClearReference?.(el.id)}
                  className="ml-1 text-blue-600 hover:text-blue-900 px-1"
                  aria-label={`Remove reference to ${el.kind}`}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <form
        className="mt-3 flex gap-2 border-t border-surface-700/50 pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
      >
        {/* Real, live-found bug (per an explicit user ask: "add wrap or something, it keeps
         * going, i cant see all the text at a time"): this used to be a single-line `<input>` —
         * a longer message just scrolled sideways inside it instead of wrapping, so most of what
         * you'd typed was invisible at once. A `<textarea>` wraps like every other message in
         * this panel already does (`whitespace-pre-wrap` on `m.text` above); auto-grows with
         * content up to a cap, then scrolls internally rather than pushing the rest of the layout
         * around. Enter still sends (matching the old input's implicit submit-on-Enter); Shift+
         * Enter inserts a real newline instead, same convention `GuardrailsSection.tsx`'s
         * edit-in-place textarea already uses. */}
        <textarea
          ref={inputRef}
          rows={1}
          className="max-h-32 flex-1 resize-none overflow-y-auto rounded-lg border border-surface-700/50 bg-surface-900/60 px-4 py-2.5 text-sm text-surface-50 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-brand-500/50 transition-all"
          placeholder={sessionId ? "Reply, or describe changes…" : "What do you want to create?"}
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
        {loading ? (
          <button
            type="button"
            onClick={async () => {
              if (!sessionId) return;
              try {
                await cancelTurn(sessionId);
                // The in-flight turn's own promise (still awaited in `handleSend`/
                // `handlePickOption`) rejects once the backend actually cancels, and THAT already
                // clears `loading` via its own `finally` block — nothing else to do here on success.
              } catch (e) {
                // Real, live-found bug (2026-09-23 frontend audit): a failed cancel call itself
                // (e.g. the backend unreachable) only logged to console — `loading` stayed `true`
                // forever with no user-visible error and no way out short of a page reload, since
                // nothing else was going to clear it. Surfaces the failure and re-enables the
                // input; if the original turn's own promise later resolves/rejects on its own, it
                // still runs its normal handling too — redundant, not harmful.
                console.error("Failed to cancel", e);
                appendError(e);
                setLoading(false);
              }
            }}
            className="rounded-lg bg-red-600/80 px-5 py-2.5 text-sm font-medium text-white hover:bg-red-500 transition-all shadow-lg hover:shadow-red-500/25"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={!input.trim()}
            className="rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50 transition-all hover:bg-brand-500 shadow-lg hover:shadow-brand-500/25 disabled:shadow-none hover:-translate-y-0.5 disabled:transform-none"
          >
            Send
          </button>
        )}
      </form>
    </div>
  );
}

export default forwardRef(ChatPanel);
