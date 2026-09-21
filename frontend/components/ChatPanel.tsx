"use client";

import { useRef, useState } from "react";
import {
  ApiError,
  IdeationOption,
  NarrativePlan,
  ScenePlan,
  SessionResponse,
  getSession,
  postTurn,
  startSession,
} from "@/lib/api";
import { assetUrl } from "@/lib/http";
import { ReferencedElement } from "@/components/CanvasView";
import { describeEvent, openEventStream } from "@/lib/events";

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
}

let nextId = 0;
function newId(): string {
  nextId += 1;
  return `m${nextId}`;
}

/**
 * Turns one real SessionResponse into what the chat should say next — status is a real backend
 * signal, not decoration. `next_prompt` is populated for ideation AND every HITL gate (the same
 * shape, per Architecture.md section 1d); it's genuinely null on "completed" (the generated
 * element itself lives on the canvas, Phase 4b, not in this response) and on some error/pending
 * cases, so those need their own honest fallback text rather than showing nothing.
 */
function describeResponse(res: SessionResponse): ChatMessage {
  // Checked before next_prompt: a real backend failure (e.g. a free-tier model returning
  // malformed JSON — a known, documented category of flakiness, not a frontend bug) still
  // populates next_prompt.message with the real error text, but status: "error" is what actually
  // says this turn failed — check status first so it renders as an error, not a normal reply.
  if (res.status === "error") {
    return { id: newId(), role: "error", text: res.next_prompt?.message || "Something went wrong on that turn." };
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
    };
  }
  if (res.next_prompt) {
    return {
      id: newId(),
      role: "assistant",
      text: res.next_prompt.message,
      options: res.next_prompt.options,
      allowFreeText: res.next_prompt.allow_free_text,
    };
  }
  if (res.status === "completed") {
    return {
      id: newId(),
      role: "assistant",
      text: "Generated — check the canvas behind this chat.",
    };
  }
  return { id: newId(), role: "assistant", text: `Status: ${res.status}` };
}

interface ChatPanelProps {
  sessionId: string | null;
  onSessionId: (id: string) => void;
  onGenerated?: () => void;
  referencedElement?: ReferencedElement | null;
  onClearReference?: () => void;
}

export default function ChatPanel({
  sessionId,
  onSessionId,
  onGenerated,
  referencedElement,
  onClearReference,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [approvalMode, setApprovalMode] = useState<"auto" | "approve">("auto");
  const [loading, setLoading] = useState(false);
  const [narration, setNarration] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  /** Opens the real SSE stream for this turn (Phase 4c) and narrates it live — only possible once
   * a sessionId already exists (the backend's own disclosed scope boundary: the very first
   * message can't be watched live since its session_id isn't known until it returns). */
  async function withNarration<T>(sid: string, fn: () => Promise<T>): Promise<T> {
    setNarration([]);
    const close = openEventStream(sid, (event) => {
      const line = describeEvent(event);
      if (line) setNarration((n) => [...n, line]);
    });
    try {
      return await fn();
    } finally {
      close();
      setNarration([]);
    }
  }

  function appendUser(text: string) {
    setMessages((m) => [...m, { id: newId(), role: "user", text }]);
  }

  function appendError(err: unknown) {
    const text = err instanceof ApiError ? `${err.message} (HTTP ${err.status})` : "Network error — is the backend running?";
    setMessages((m) => [...m, { id: newId(), role: "error", text }]);
  }

  async function handleSend(freeTextOverride?: string) {
    const text = freeTextOverride ?? input.trim();
    if (!text || loading) return;
    setInput("");
    appendUser(text);
    setLoading(true);
    try {
      const res = sessionId
        ? await withNarration(sessionId, () =>
            postTurn(sessionId, { freeText: text, referencedElementId: referencedElement?.id }),
          )
        : await startSession(text, approvalMode);
      if (!sessionId) onSessionId(res.id);
      setMessages((m) => [...m, describeResponse(res)]);
      if (res.status === "completed") onGenerated?.();
      onClearReference?.(); // one-shot per message, same as the existing Comment action
    } catch (err) {
      appendError(err);
    } finally {
      setLoading(false);
    }
  }

  async function handlePickOption(option: IdeationOption) {
    if (!sessionId || loading) return;
    appendUser(option.label);
    setLoading(true);
    try {
      const res = await withNarration(sessionId, () =>
        postTurn(sessionId, { pickedOptionId: option.id, referencedElementId: referencedElement?.id }),
      );
      setMessages((m) => [...m, describeResponse(res)]);
      if (res.status === "completed") onGenerated?.();
      onClearReference?.();
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

  return (
    <div className="flex h-full w-full flex-col rounded-2xl border border-neutral-200 bg-white/95 p-4 shadow-2xl backdrop-blur">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-neutral-200 pb-3">
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
          <p className="text-sm text-neutral-500">
            Describe what you want — e.g. &ldquo;a hero shot for a red running sneaker&rdquo;.
          </p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div
              className={
                "max-w-[80%] rounded-2xl px-4 py-2 text-sm " +
                (m.role === "user"
                  ? "bg-neutral-900 text-white"
                  : m.role === "error"
                    ? "bg-red-100 text-red-800"
                    : m.role === "gate"
                      ? m.gateStage === "motion_pending"
                        ? "bg-red-50 text-neutral-900 shadow-sm ring-2 ring-red-300"
                        : "bg-amber-50 text-neutral-900 shadow-sm ring-2 ring-amber-300"
                      : "bg-white text-neutral-900 shadow-sm ring-1 ring-neutral-200")
              }
            >
              {m.role === "gate" && (
                <p className="mb-1 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-amber-800">
                  ⏸ {m.gateStage === "motion_pending" ? "Spend approval needed" : "Approval needed"}
                </p>
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
                        "flex items-start gap-2 rounded-lg border px-3 py-2 text-left text-sm disabled:opacity-50 " +
                        (m.gateStage === "motion_pending" && opt.id === "approve"
                          ? "border-red-300 bg-red-100 hover:bg-red-200"
                          : "border-neutral-300 bg-neutral-50 hover:bg-neutral-100")
                      }
                    >
                      <span className="font-semibold text-neutral-400">{i + 1}.</span>
                      <span>
                        <span className="block font-medium">{opt.label}</span>
                        <span className="block text-xs text-neutral-500">{opt.description}</span>
                      </span>
                    </button>
                  ))}
                  {m.allowFreeText !== false && (
                    <button
                      onClick={handleOther}
                      disabled={loading}
                      className="flex items-start gap-2 rounded-lg border border-dashed border-neutral-300 bg-neutral-50 px-3 py-2 text-left text-sm hover:bg-neutral-100 disabled:opacity-50"
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
        ))}
        {loading && (
          <div className="space-y-0.5 text-xs text-neutral-500">
            {narration.length === 0 ? (
              <p className="text-neutral-400">Working…</p>
            ) : (
              narration.map((line, i) => <p key={i}>{line}</p>)
            )}
          </div>
        )}
      </div>

      {referencedElement && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-blue-300 bg-blue-50 px-2 py-1.5 text-xs">
          {referencedElement.kind === "video" ? (
            <video src={referencedElement.url} className="h-8 w-8 rounded object-cover" />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element -- a dynamic, backend-served asset thumbnail
            <img src={referencedElement.url} alt="" className="h-8 w-8 rounded object-cover" />
          )}
          <span className="flex-1 text-blue-800">
            Referencing this {referencedElement.kind} — your next message will be about it
          </span>
          <button onClick={onClearReference} className="text-blue-600 hover:text-blue-900" aria-label="Clear reference">
            ×
          </button>
        </div>
      )}

      <form
        className="mt-3 flex gap-2 border-t border-neutral-200 pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
      >
        <input
          ref={inputRef}
          className="flex-1 rounded-lg border border-neutral-300 px-3 py-2 text-sm"
          placeholder={sessionId ? "Reply, or describe changes…" : "What do you want to create?"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  );
}
