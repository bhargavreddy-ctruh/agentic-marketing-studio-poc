"use client";

import { forwardRef, memo, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  ApiError,
  ChatTurn,
  IdeationOption,
  NarrativePlan,
  ScenePlan,
  SessionResponse,
  createSession,
  crawlUrl,
  getSession,
  listTurns,
  postTurn,
  cancelTurn,
} from "@/lib/api";
import { assetUrl } from "@/lib/http";
import { ReferencedElement, elementKind } from "@/components/CanvasView";
import { LiveEvent, PipelineNode, PlanStep, describeEvent, openEventStream } from "@/lib/events";
import { CanvasElement, getCanvasState, uploadAndPlaceElement } from "@/lib/canvas";

import { ChatMessage, GateStage, ChatBubble, newId, isGatewayTimeoutLikeError, truncate } from "@/components/chat/ChatBubble";
import { ChatInput } from "@/components/chat/ChatInput";
import { useDragDrop } from "@/hooks/useDragDrop";
import { useChatStream } from "@/hooks/useChatStream";

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
  /** Appends one uploaded attachment (paperclip/paste/drop on the composer) to the chat's current
   * reference selection — a real, live-found gap (fidelity audit 2026-10-05): the composer had no
   * attach path at all, so a user who said "apply my image" with nothing already on the canvas had
   * no way to give the agent that image except first uploading it onto the canvas separately and
   * then clicking "reference" there. Reuses the exact same `uploadAndPlaceElement` +
   * `ReferencedElement` shape `CanvasView`'s own tile-click reference flow already uses. */
  onAddReferenceElement?: (element: ReferencedElement) => void;
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
  isMaximized?: boolean;
  onToggleMaximize?: () => void;
  /** The SAME `pipelineNodes` (`buildPipelineNodes(turnEvents)`) `page.tsx` already computes once
   * and passes to `AgentHUD` — reused here, not recomputed, so the plan-preview card's live
   * per-step progress badges ("update the progress also," 2026-10-06) come from the exact same
   * real event-derived data every other progress indicator in this app already uses. */
  pipelineNodes?: PipelineNode[];
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
  { sessionId, onSessionId, onGenerated, referencedElements, onClearReference, onAddReferenceElement, onTurnEvent, onRestoreEvents, isMaximized, onToggleMaximize, pipelineNodes }: ChatPanelProps,
  ref: React.ForwardedRef<ChatPanelHandle>,
) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoadingState] = useState(false);
  const loadingRef = useRef(false);
  function setLoading(v: boolean) { loadingRef.current = v; setLoadingState(v); }
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { attaching, attachFile, handlePaste, handleDrop } = useDragDrop(sessionId, onAddReferenceElement, onGenerated);


  const { narration, setNarration, liveThinking, withNarration } = useChatStream(setMessages, onTurnEvent);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Chat history lazy-load (2026-10-05, per an explicit user ask: "add lazy loading to chat box,
  // but load initial things immidiately") — the initial mount now asks the backend for only the
  // most recent `INITIAL_TURN_LIMIT` turns (fast, same shape of fix as the canvas's own
  // performance round earlier this session), instead of the whole session history every time.
  // Older turns page in on demand — scrolling near the top of the list, or the explicit button —
  // rather than being paid for up front on every single load.
  const INITIAL_TURN_LIMIT = 20;
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [loadingMoreHistory, setLoadingMoreHistory] = useState(false);
  // The oldest turn id currently loaded — the real pagination cursor (`before_id`) for the next
  // "load earlier" fetch. A ref, not state: it's read inside an event handler, never rendered.
  const oldestTurnIdRef = useRef<string | null>(null);
  const historyScrollRef = useRef<HTMLDivElement>(null);
  // Prepending older messages must NOT trigger the "always scroll to the latest message" effect
  // below — that effect fires on every `messages` change, which would otherwise yank the view back
  // to the bottom the instant older history loads, undoing the very scroll-up gesture that
  // triggered the load. Set right before a prepend, consumed (and cleared) by that effect once.
  const suppressAutoScrollRef = useRef(false);

  const [activeTab, setActiveTab] = useState<"Chat" | "Assets" | "Plan">("Chat");
  const [canvasAssets, setCanvasAssets] = useState<CanvasElement[]>([]);
  const [loadingAssets, setLoadingAssets] = useState(false);
  // Real session title from the backend (4a) — replaces the hard-coded placeholder.
  const [sessionTitle, setSessionTitle] = useState<string>("Untitled workflow");

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
  // Tracks the product id of the last referenced element so continuations of a prompt
  // (e.g. answering a clarifying question) keep the same grouping as the turn that started it.
  const lastReferencedProductIdRef = useRef<string | null | undefined>(undefined);

  // Real, live-found bug (2026-10-06): the Stop button optimistically appends its own
  // "Generation cancelled by user." bubble and unlocks the UI immediately, but `handleSend`/
  // `handlePickOption` are still awaiting `withNarration` underneath — the backend's own real
  // `CancelledError` handling (`session_service.py`'s `_run_turn`) still emits a genuine
  // `turn_completed` event with that same message, which resolves the pending promise and
  // appends a SECOND, duplicate bubble once it arrives. Set by Stop, checked (and cleared) right
  // where that result would otherwise be appended, in both call sites.
  const cancelledRef = useRef(false);

  // Phase 1 gap-close (2026-09-28, combined grouping plan) — the one real ChatPanel gap:
  // referencing an element always silently inherited its product, with no way to say "generate
  // something new instead" while still referencing an asset for context. Defaults false (today's
  // inherit-by-default behavior, unchanged); reset after every send, same lifecycle as the
  // reference chip itself, so it never silently leaks into a later, unrelated turn.
  const [startNewProduct, setStartNewProduct] = useState(false);

  // Product/Brand crawler "🔗 Add Link" popover (2026-09-28).
  const [showLinkPopover, setShowLinkPopover] = useState(false);
  const [linkInput, setLinkInput] = useState("");
  const [linkCrawling, setLinkCrawling] = useState(false);

  async function handleTriggerLinkCrawl() {
    const url = linkInput.trim();
    if (!url || linkCrawling || !sessionId) return;
    setLinkCrawling(true);
    try {
      await crawlUrl(sessionId, url);
      setMessages((m) => [...m, { id: newId(), role: "assistant", text: `🔗 Crawling ${url} — extracting DNA… (progress shows above as it runs)` }]);
      setLinkInput("");
      setShowLinkPopover(false);
    } catch (err) {
      appendError(err);
    } finally {
      setLinkCrawling(false);
    }
  }

  // Always show the latest chat content (2026-09-22, per an explicit user ask) — scrolls the
  // message list to the bottom whenever anything new appears: a message, live-streamed thinking,
  // or a narration status line, so the user never has to manually scroll down to see what just
  // arrived.
  useEffect(() => {
    if (suppressAutoScrollRef.current) {
      suppressAutoScrollRef.current = false;
      return;
    }
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, liveThinking, narration, loading]);



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
    // Hoisted above the try (2026-09-30) so the catch block below can still reach the real
    // session id for the gateway-timeout reconnect fallback — a `let` declared inside `try` isn't
    // visible in its own `catch`.
    let sid: string | null = sessionId;
    try {
      // Turn 1 streams exactly like every later turn now: create the (empty) session first so
      // the SSE stream can open against a real id, THEN send the message as a normal turn —
      // fixes live "thinking"/Node Mode being empty for a session's very first message.
      if (!sid) {
        const created = await createSession();
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
      // Derive targetProductId from the referenced elements' own product. Prefer the freshly
      // referenced one; fall back to the last-used one for continuations (same turn, same reference).
      const freshProductId = referencedElements?.find(e => e.productId)?.productId ?? null;
      const productIdToSend = freshIds && freshIds.length > 0
        ? freshProductId
        : wasContinuation ? lastReferencedProductIdRef.current : freshProductId;
      lastReferencedIdsRef.current = idsToSend;
      lastReferencedProductIdRef.current = productIdToSend;
      onClearReference?.();
      const useStartNewProduct = startNewProduct;
      setStartNewProduct(false);

      const { result: res, thinking, seconds } = await withNarration(sid, () =>
        postTurn(sid!, {
          freeText: text,
          referencedElementIds: idsToSend,
          // Pass the product group so the generated element lands in the same group as the
          // referenced source. Omit (undefined) when startNewProduct is true so the backend's
          // own "start new product" logic takes over instead.
          targetProductId: useStartNewProduct ? undefined : (productIdToSend ?? undefined),
          startNewProduct: useStartNewProduct,
        }),
      );
      // See `cancelledRef`'s own comment above — a user-initiated Stop already appended its own
      // cancellation bubble optimistically; skip appending this turn's real (duplicate) one.
      if (cancelledRef.current) {
        cancelledRef.current = false;
      } else {
        setMessages((m) => [...m, describeResponse(res, thinking, seconds)]);
        if (res.status === "completed") onGenerated?.();
      }
    } catch (err) {
      // Real, live-found bug (2026-10-06): this used to special-case a gateway-timeout-shaped
      // error here with its own separate reconnect-and-poll fallback (`pollUntilResolved`,
      // `looksLikeGatewayTimeout`) — but `withNarration` above already owns that exact case
      // internally now that the backend's turn handling is fire-and-forget: it swallows a
      // timeout-shaped rejection from `fn()` and waits on the real `turn_completed` SSE event
      // instead of ever rejecting here for that case. An error actually reaching this catch block
      // is a genuine failure (the POST itself never even started the turn), not a slow backend
      // still working — always surfaced directly, same as `loadHistory`'s own reconnect path
      // already does for the equivalent "still generating" case on a page refresh.
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
      const freshProductId = referencedElements?.find(e => e.productId)?.productId ?? null;
      const productIdToSend = freshIds && freshIds.length > 0
        ? freshProductId
        : wasContinuation ? lastReferencedProductIdRef.current : freshProductId;
      lastReferencedIdsRef.current = idsToSend;
      lastReferencedProductIdRef.current = productIdToSend;
      onClearReference?.();
      const useStartNewProduct = startNewProduct;
      setStartNewProduct(false);

      const { result: res, thinking, seconds } = await withNarration(sessionId, () =>
        postTurn(sessionId, {
          pickedOptionId: option.id,
          referencedElementIds: idsToSend,
          targetProductId: useStartNewProduct ? undefined : (productIdToSend ?? undefined),
          startNewProduct: useStartNewProduct,
        }),
      );
      // See `cancelledRef`'s own comment above — a user-initiated Stop already appended its own
      // cancellation bubble optimistically; skip appending this turn's real (duplicate) one.
      if (cancelledRef.current) {
        cancelledRef.current = false;
      } else {
        setMessages((m) => [...m, describeResponse(res, thinking, seconds)]);
        if (res.status === "completed") onGenerated?.();
      }
    } catch (err) {
      // See the matching comment in `handleSend`'s own catch block above — `withNarration`
      // already owns the "proxy gave up but the backend is still working" case internally now.
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

  async function loadHistory(isCancelled?: () => boolean, signal?: AbortSignal) {
    if (!sessionId) return;
    try {
      const [session, turns] = await Promise.all([
        getSession(sessionId, signal),
        listTurns(sessionId, { limit: INITIAL_TURN_LIMIT, signal }),
      ]);
      if (isCancelled?.()) return;

      // Wire the real session title (4a) — already fetched above, just never stored before.
      setSessionTitle(session.title ?? "Untitled workflow");

      const restored: ChatMessage[] = turns.flatMap((turn: ChatTurn) => [
        { id: newId(), role: "user" as const, text: turn.user_text, referencedElements: turn.referenced_elements },
        // The plan-preview bubble, reconstructed from its own persisted field (2026-10-06) —
        // reproduces the exact live ordering (user -> plan -> result) so a refresh shows the same
        // conversation shape the live view did.
        ...(turn.plan && turn.plan.length > 0
          ? [{ id: newId(), role: "plan" as const, text: "", planSteps: turn.plan }]
          : []),
        { id: newId(), role: "assistant" as const, text: turn.assistant_text ?? "", thinking: turn.thinking_text ?? undefined },
      ]);

      // A full page back (same size as what was asked for) means there's likely more behind it;
      // a short page means we've already seen every turn this session has. Re-derived fresh on
      // every full reload (including a manual refresh), same as `hasMoreHistory` itself.
      oldestTurnIdRef.current = turns[0]?.id ?? null;
      setHasMoreHistory(turns.length === INITIAL_TURN_LIMIT);

      const lastTurnForNode = turns[turns.length - 1];
      if (lastTurnForNode) onRestoreEvents?.(lastTurnForNode.events as LiveEvent[]);

      if (session.status === "generating") {
        restored.push({ id: newId(), role: "assistant", text: "Reconnecting — a generation is still in progress…" });
        setMessages(restored);
        return;
      }

      if (turns.length === 0) {
        setMessages([]);
        return;
      }
      restored[restored.length - 1] = describeResponse(session, turns[turns.length - 1].thinking_text ?? undefined);
      setMessages(restored);
    } catch (err) {
      // A real `AbortController`-driven cancellation (2026-10-05, same fix shape as
      // `CanvasView.tsx`'s own mount effect) — Strict Mode's first invocation gets aborted by the
      // second, same as every other fetch here; nothing to show the user for that, it's expected.
      if (err instanceof DOMException && err.name === "AbortError") return;
      appendError(err);
    }
  }

  /** Pages one older batch of turns in from `oldestTurnIdRef.current` and prepends them — the
   * on-demand half of the lazy-load (initial load above only ever fetches the most recent
   * `INITIAL_TURN_LIMIT`). Preserves the user's scroll position across the prepend (otherwise the
   * container's native "stick near the top" behavior would shove the view back down to whatever
   * was on screen a moment ago, right as new content appears above it). */
  async function loadEarlierHistory() {
    if (!sessionId || loadingMoreHistory || !hasMoreHistory || !oldestTurnIdRef.current) return;
    setLoadingMoreHistory(true);
    const container = historyScrollRef.current;
    const prevScrollHeight = container?.scrollHeight ?? 0;
    const prevScrollTop = container?.scrollTop ?? 0;
    try {
      const older = await listTurns(sessionId, {
        limit: INITIAL_TURN_LIMIT,
        beforeId: oldestTurnIdRef.current,
      });
      if (older.length === 0) {
        setHasMoreHistory(false);
        return;
      }
      const olderMessages: ChatMessage[] = older.flatMap((turn: ChatTurn) => [
        { id: newId(), role: "user" as const, text: turn.user_text, referencedElements: turn.referenced_elements },
        ...(turn.plan && turn.plan.length > 0
          ? [{ id: newId(), role: "plan" as const, text: "", planSteps: turn.plan }]
          : []),
        { id: newId(), role: "assistant" as const, text: turn.assistant_text ?? "", thinking: turn.thinking_text ?? undefined },
      ]);
      oldestTurnIdRef.current = older[0]?.id ?? oldestTurnIdRef.current;
      setHasMoreHistory(older.length === INITIAL_TURN_LIMIT);
      suppressAutoScrollRef.current = true;
      setMessages((m) => [...olderMessages, ...m]);
      requestAnimationFrame(() => {
        if (container) container.scrollTop = prevScrollTop + (container.scrollHeight - prevScrollHeight);
      });
    } catch (err) {
      appendError(err);
    } finally {
      setLoadingMoreHistory(false);
    }
  }

  function handleHistoryScroll(e: React.UIEvent<HTMLDivElement>) {
    if (e.currentTarget.scrollTop < 80 && hasMoreHistory && !loadingMoreHistory) {
      void loadEarlierHistory();
    }
  }

  async function handleRefresh() {
    await loadHistory();
  }

  async function handleCancelTurn() {
    if (!sessionId) return;
    cancelledRef.current = true;
    setLoading(false);
    setMessages((m) => [
      ...m.filter(
        (msg) =>
          !msg.text.startsWith("Reconnecting") &&
          !msg.text.startsWith("Still working"),
      ),
      { id: newId(), role: "assistant", text: "Generation cancelled by user." },
    ]);
    try {
      await cancelTurn(sessionId);
    } catch (e) {
      console.error("Failed to cancel", e);
      appendError(e);
    }
  }

  useEffect(() => {
    let cancelled = false;
    // Real, live-found gap (2026-10-05): `cancelled` alone only suppressed the STATE UPDATE for a
    // Strict-Mode-superseded call — the actual `getSession`/`listTurns` network requests (and the
    // DB connections they hold) still went out and completed a second time regardless, doubling
    // real load on an already deliberately small connection pool shared with production
    // (`models/base.py`'s `pool_size=3, max_overflow=2`). A real `AbortController` (same fix shape
    // already proven in `CanvasView.tsx`'s own mount effect) now cancels the IN-FLIGHT request
    // itself, not just its effect on state.
    const controller = new AbortController();
    loadHistory(() => cancelled, controller.signal);
    return () => {
      cancelled = true;
      controller.abort();
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
  // Only the MOST RECENT plan bubble gets live/restored progress badges below — `pipelineNodes`
  // always reflects whichever turn's events `page.tsx` currently has loaded (the live in-flight
  // turn, or the last turn's restored events), never older turns', so applying it to an older
  // plan bubble could show a stale/wrong status borrowed from an unrelated later turn.
  const lastPlanMessageId = messages.filter((mm) => mm.role === "plan").at(-1)?.id;

  return (
    <div className={`flex h-full w-full flex-col glass-panel p-4 overflow-hidden font-sans transition-all duration-300 ${
      isMaximized
        ? "rounded-none border-r border-surface-700/50"
        : "rounded-2xl border border-surface-700/50"
    }`}>
      <header className="mb-3 flex flex-col gap-4 border-b border-surface-700/50 pb-4">
        <div className="flex items-center justify-between text-sm font-medium text-surface-400">
          <div className="flex gap-2">
            <button 
              onClick={() => setActiveTab("Chat")}
              className={`rounded-full px-4 py-1.5 transition-colors ${activeTab === "Chat" ? "bg-surface-800 text-surface-50 shadow-sm ring-1 ring-surface-700" : "hover:text-surface-50"}`}>
              Chat
            </button>
            <button 
              onClick={() => setActiveTab("Assets")}
              className={`rounded-full px-4 py-1.5 transition-colors ${activeTab === "Assets" ? "bg-surface-800 text-surface-50 shadow-sm ring-1 ring-surface-700" : "hover:text-surface-50"}`}>
              Assets
            </button>
            <button 
              onClick={() => setActiveTab("Plan")}
              className={`rounded-full px-4 py-1.5 transition-colors ${activeTab === "Plan" ? "bg-surface-800 text-surface-50 shadow-sm ring-1 ring-surface-700" : "hover:text-surface-50"}`}>
              Plan
            </button>
          </div>
          {onToggleMaximize && (
            <button
              onClick={onToggleMaximize}
              className="rounded-lg p-1.5 text-surface-400 transition-colors hover:bg-surface-800 hover:text-surface-50"
              title={isMaximized ? "Restore floating panel" : "Maximize to sidebar"}
              aria-label={isMaximized ? "Restore floating panel" : "Maximize to sidebar"}
            >
              {isMaximized ? (
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 4.5v4.5m0 0H4.5m4.5 0L3.5 3.5m11 16v-4.5m0 0h4.5m-4.5 0l5.5 5.5M4.5 15h4.5m0 0v4.5m0-4.5l-5.5 5.5M19.5 9h-4.5m0 0V4.5m0 4.5l5.5-5.5" />
                </svg>
              ) : (
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25v-4.5m0 4.5h-4.5m4.5 0L15 15m-11.25 5.25h4.5m-4.5 0v-4.5m0 4.5L9 15" />
                </svg>
              )}
            </button>
          )}
        </div>
        
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 cursor-pointer hover:opacity-80 transition-opacity">
            <h1 className="text-base font-semibold text-surface-50 sm:text-lg">{sessionTitle}</h1>
            <svg className="h-4 w-4 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
          {sessionId && (
            <div className="flex items-center gap-3 text-xs text-surface-500">
              <span className="font-mono">ID: {sessionId.slice(0, 8)}</span>
              <button onClick={handleRefresh} className="hover:text-surface-50 transition-colors">
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
              </button>
            </div>
          )}
        </div>
      </header>

      {activeTab === "Chat" && (
        <div
          ref={historyScrollRef}
          onScroll={handleHistoryScroll}
          className="flex-1 space-y-4 overflow-y-auto pr-1 scrollbar-thin scrollbar-track-transparent scrollbar-thumb-surface-700"
        >
          {hasMoreHistory && (
            <div className="flex justify-center pb-2">
              <button
                type="button"
                onClick={loadEarlierHistory}
                disabled={loadingMoreHistory}
                className="rounded-full border border-surface-700/50 bg-surface-800/50 px-3 py-1 text-xs text-surface-400 transition-colors hover:text-surface-200 disabled:opacity-50"
              >
                {loadingMoreHistory ? "Loading earlier messages…" : "Load earlier messages"}
              </button>
            </div>
          )}
          {messages.length === 0 && (
            <div className="flex h-full items-center justify-center">
            <p className="text-sm text-surface-500 text-center max-w-xs">
              Describe what you want to build or generate. I can help you plan, write, and create visual assets.
            </p>
          </div>
        )}
        {messages.map((m) => (
          <ChatBubble
            key={m.id}
            m={m}
            lastPlanMessageId={lastPlanMessageId}
            pipelineNodes={pipelineNodes}
            handlePickOption={handlePickOption}
            handleOther={handleOther}
            loading={loading}
          />
        ))}
        {loading && (
          <div className="flex items-center gap-3 text-xs text-surface-500 pl-2 animate-in fade-in duration-300">
            <svg className="w-4 h-4 animate-spin text-surface-600" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <div className="flex flex-col gap-1 w-full max-w-full">
              <div className="flex items-center gap-2">
                {narration.length === 0 ? (
                  <span className="italic">Imagining...</span>
                ) : (
                  <span className="italic text-surface-400 font-medium">{narration[narration.length - 1]}</span>
                )}
              </div>
              {liveThinking && (
                <div className="text-[10px] text-surface-500 font-mono whitespace-pre-wrap max-h-48 overflow-y-auto pl-2 border-l-2 border-surface-700/50 mt-1">
                  {liveThinking.replace(/<\/?thought>/g, '')}
                </div>
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
          {referencedElements.some((el) => el.productId) && (
            <div className="flex items-center gap-2 pt-1 border-t border-surface-700/50">
              <span className="text-surface-500">Target product:</span>
              <button
                type="button"
                onClick={() => setStartNewProduct(false)}
                className={`rounded-full px-2.5 py-1 transition-colors ${
                  !startNewProduct
                    ? "bg-surface-700 text-surface-100"
                    : "text-surface-500 hover:text-surface-300"
                }`}
              >
                Keep in {referencedElements.find((el) => el.productId)?.productName ?? "this product"}
              </button>
              <button
                type="button"
                onClick={() => setStartNewProduct(true)}
                className={`rounded-full px-2.5 py-1 transition-colors ${
                  startNewProduct
                    ? "bg-surface-700 text-surface-100"
                    : "text-surface-500 hover:text-surface-300"
                }`}
              >
                + Start new product
              </button>
            </div>
          )}
        </div>
      )}

      <ChatInput
        input={input}
        setInput={setInput}
        inputRef={inputRef}
        fileInputRef={fileInputRef}
        loading={loading}
        attaching={attaching}
        sessionId={sessionId}
        handleSend={handleSend}
        handlePaste={handlePaste}
        handleDrop={handleDrop}
        attachFile={attachFile}
        showLinkPopover={showLinkPopover}
        setShowLinkPopover={setShowLinkPopover}
        linkInput={linkInput}
        setLinkInput={setLinkInput}
        linkCrawling={linkCrawling}
        handleTriggerLinkCrawl={handleTriggerLinkCrawl}
        handleCancelTurn={handleCancelTurn}
      />
    </div>
  );
}

// React.memo (2026-10-05, FRONTEND_AUDIT.md #13) — wraps the forwardRef result; only effective
// once page.tsx (its sole caller) also stabilizes the inline callback props it passes down.
export default memo(forwardRef(ChatPanel));
