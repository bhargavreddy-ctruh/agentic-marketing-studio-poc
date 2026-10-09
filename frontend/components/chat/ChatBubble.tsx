"use client";

import { useState } from "react";
import { ReferencedElement } from "@/components/CanvasView";
import { PlanStep, PipelineNode } from "@/lib/events";
import { assetUrl } from "@/lib/http";
import { IdeationOption, NarrativePlan, ScenePlan } from "@/lib/api";

export type GateStage = "narrative_pending" | "scene_pending" | "motion_pending";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "error" | "gate" | "plan";
  text: string;
  options?: IdeationOption[];
  allowFreeText?: boolean;
  gateStage?: GateStage;
  narrativePlan?: NarrativePlan;
  scenePlan?: ScenePlan;
  planSteps?: PlanStep[];
  referencedElements?: ReferencedElement[];
  thinking?: string;
  thinkingSeconds?: number;
  formatBadge?: string;
}

export function newId(): string {
  return crypto.randomUUID();
}

import { ApiError } from "@/lib/api";

export function isGatewayTimeoutLikeError(err: unknown): boolean {
  if (err instanceof ApiError) {
    return [502, 503, 504].includes(err.status) || (err.status === 500 && err.message === "Internal Server Error");
  }
  return err instanceof Error && (err.message.includes("fetch failed") || err.message.includes("hang up"));
}

export function truncate(text: string, maxChars: number): string {
  const t = text.trim();
  return t.length > maxChars ? `${t.slice(0, maxChars - 1).trimEnd()}…` : t;
}

function humanizeSpecialist(name: string): string {
  return name
    .split("_")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

function groupPlanSteps(plan: PlanStep[]): PlanStep[][] {
  return groupPlanStepsWithIndex(plan).map((group) => group.map((g) => g.step));
}

/** Same grouping as `groupPlanSteps`, but keeps each step's index into the ORIGINAL (ungrouped)
 * array alongside it — the plan editor below needs that real index to mutate the right entry in
 * `draftSteps`, since `parallel_group` reshuffles display order away from array order. */
function groupPlanStepsWithIndex(plan: PlanStep[]): { step: PlanStep; index: number }[][] {
  const groups: { step: PlanStep; index: number }[][] = [];
  const indexByGroup = new Map<number, number>();
  plan.forEach((step, index) => {
    const gid = step.parallel_group;
    if (gid == null) {
      groups.push([{ step, index }]);
      return;
    }
    const existing = indexByGroup.get(gid);
    if (existing != null) {
      groups[existing].push({ step, index });
    } else {
      indexByGroup.set(gid, groups.length);
      groups.push([{ step, index }]);
    }
  });
  return groups;
}

function planStepStatus(
  step: PlanStep,
  pipelineNodes?: PipelineNode[],
): PipelineNode["status"] | undefined {
  return pipelineNodes?.find((n) => n.label === step.specialist)?.status;
}

interface ChatBubbleProps {
  m: ChatMessage;
  lastPlanMessageId?: string;
  pipelineNodes?: PipelineNode[];
  // Second arg is the user's own edited plan (ChatBubble's inline editor below) — only ever
  // non-undefined for the "approve" option on a bubble whose plan is still editable; every other
  // call site (cancel, any non-plan option) passes nothing, unchanged from before this existed.
  handlePickOption?: (opt: IdeationOption, editedPlan?: PlanStep[]) => void;
  handleOther?: () => void;
  loading?: boolean;
}

export function ChatBubble({ m, lastPlanMessageId, pipelineNodes, handlePickOption, handleOther, loading }: ChatBubbleProps) {
  const [isScrollMode, setIsScrollMode] = useState(true);
  // A plan is only ever editable on the live bubble still awaiting the user's approve/cancel
  // choice (explicit user ask: "make sure user can directly edit the plan if they dont find it
  // fitting users goal") — a restored/completed turn's plan bubble carries no `options`, so this
  // is naturally false there, same signal `m.role === "plan"` bubbles already rely on implicitly.
  const canEditPlan = Boolean(m.planSteps && m.planSteps.length > 0 && m.options?.some((o) => o.id === "approve"));
  const [isEditingPlan, setIsEditingPlan] = useState(false);
  // Seeded once from the proposed plan and mutated locally — `postTurn` only ever sees this when
  // the user actually clicks Approve, never streamed back mid-edit, so there's no backend call per
  // keystroke. Initialized lazily (function form) so it only runs once per bubble instance, not on
  // every re-render (e.g. while `pipelineNodes` status ticks update this same component).
  const [draftSteps, setDraftSteps] = useState<PlanStep[]>(() => (m.planSteps ? m.planSteps.map((s) => ({ ...s })) : []));
  const hasEdits = canEditPlan && JSON.stringify(draftSteps) !== JSON.stringify(m.planSteps);
  const displaySteps = canEditPlan ? draftSteps : m.planSteps;

  function updateDraftInstruction(index: number, instruction: string) {
    setDraftSteps((prev) => prev.map((s, i) => (i === index ? { ...s, instruction } : s)));
  }

  function removeDraftStep(index: number) {
    setDraftSteps((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== index) : prev));
  }

  function resetDraftSteps() {
    setDraftSteps(m.planSteps ? m.planSteps.map((s) => ({ ...s })) : []);
  }

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300">
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
                  : m.role === "plan"
                    ? "bg-surface-800/50 text-surface-200 border border-surface-700/50"
                    : "text-surface-200")
          }
        >
          {m.role === "gate" && (
            <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-surface-400">
              <span className="flex h-4 w-4 items-center justify-center rounded-full bg-surface-700 text-[10px]">⏸</span>
              {m.gateStage === "motion_pending" ? "Spend approval needed" : "Approval needed"}
            </p>
          )}
          {displaySteps && displaySteps.length > 0 && (
            <div className="mb-3">
              <div className="mb-2 flex items-center justify-between">
                <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-surface-400">
                  <span className="flex h-4 w-4 items-center justify-center rounded-full bg-surface-700 text-[10px]">📋</span>
                  Proposed Plan ({displaySteps.length} {displaySteps.length === 1 ? "step" : "steps"})
                  {hasEdits && <span className="normal-case text-amber-400/90 font-normal">· edited</span>}
                </p>
                <div className="flex items-center gap-1.5">
                  {canEditPlan && hasEdits && !isEditingPlan && (
                    <button
                      type="button"
                      onClick={resetDraftSteps}
                      className="text-[11px] font-medium text-surface-400 hover:text-surface-200 transition-colors px-2 py-0.5 rounded bg-surface-800/80 border border-surface-700/50"
                      title="Discard your edits and restore the originally proposed plan"
                    >
                      Reset
                    </button>
                  )}
                  {canEditPlan && (
                    <button
                      type="button"
                      onClick={() => setIsEditingPlan(!isEditingPlan)}
                      className={
                        "text-[11px] font-medium transition-colors flex items-center gap-1 px-2 py-0.5 rounded border " +
                        (isEditingPlan
                          ? "text-emerald-300 bg-emerald-950/40 border-emerald-800/40 hover:bg-emerald-900/40"
                          : "text-surface-400 hover:text-surface-200 bg-surface-800/80 border-surface-700/50")
                      }
                      title={isEditingPlan ? "Finish editing" : "Doesn't fit your goal? Edit any step before approving"}
                    >
                      {isEditingPlan ? "✓ Done" : "✎ Edit plan"}
                    </button>
                  )}
                  {!isEditingPlan && displaySteps.length > 2 && (
                    <button
                      type="button"
                      onClick={() => setIsScrollMode(!isScrollMode)}
                      className="text-[11px] font-medium text-surface-400 hover:text-surface-200 transition-colors flex items-center gap-1 px-2 py-0.5 rounded bg-surface-800/80 border border-surface-700/50"
                      title={isScrollMode ? "Show all steps in full" : "Switch to scrollable view"}
                    >
                      {isScrollMode ? "↕ Expand view" : "↕ Scroll view"}
                    </button>
                  )}
                </div>
              </div>
              <div
                className={`rounded-xl border border-surface-700/50 bg-surface-900/60 p-3 text-xs shadow-inner space-y-2.5 transition-all ${
                  isScrollMode && !isEditingPlan ? "max-h-80 overflow-y-auto pr-1.5 overscroll-contain" : ""
                }`}
              >
                {groupPlanStepsWithIndex(displaySteps).map((group, gi) => (
                  <div key={gi} className="space-y-2">
                    {group.map(({ step, index }) => {
                      const status = m.id === lastPlanMessageId ? planStepStatus(step, pipelineNodes) : undefined;
                      return (
                        <div
                          key={index}
                          className="rounded-lg bg-surface-800/40 border border-surface-700/40 p-2.5 space-y-1.5 transition-colors hover:bg-surface-800/60"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-semibold text-surface-200 text-xs px-2 py-0.5 rounded bg-surface-700/50 border border-surface-600/40">
                                {humanizeSpecialist(step.specialist)}
                              </span>
                              {group.length > 1 && (
                                <span className="text-[10px] font-medium uppercase tracking-wide text-amber-400/90 bg-amber-950/40 px-1.5 py-0.5 rounded border border-amber-800/30">
                                  ⚡ parallel
                                </span>
                              )}
                            </div>
                            {isEditingPlan ? (
                              <button
                                type="button"
                                onClick={() => removeDraftStep(index)}
                                disabled={draftSteps.length <= 1}
                                title={draftSteps.length <= 1 ? "A plan needs at least one step" : "Remove this step"}
                                className="shrink-0 text-[11px] text-red-400/80 hover:text-red-300 disabled:opacity-30 disabled:cursor-not-allowed px-1.5 py-0.5 rounded hover:bg-red-950/30"
                              >
                                ✕ Remove
                              </button>
                            ) : (
                              <>
                                {status === "running" && (
                                  <span className="shrink-0 flex items-center gap-1.5 text-[11px] text-amber-400 font-medium">
                                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span>
                                    In progress
                                  </span>
                                )}
                                {status === "completed" && (
                                  <span className="shrink-0 text-emerald-400 text-xs font-medium">✓ Completed</span>
                                )}
                                {status === "failed" && (
                                  <span className="shrink-0 text-red-400 text-xs font-medium">✗ Failed</span>
                                )}
                                {!status && (
                                  <span className="shrink-0 text-surface-500 text-[10px]">Queued</span>
                                )}
                              </>
                            )}
                          </div>
                          {isEditingPlan ? (
                            <textarea
                              value={step.instruction ?? ""}
                              onChange={(e) => updateDraftInstruction(index, e.target.value)}
                              rows={3}
                              placeholder="What should this step do?"
                              className="w-full resize-y rounded-md bg-surface-900/70 border border-surface-700/60 text-surface-200 text-xs leading-relaxed p-2 focus:outline-none focus:ring-1 focus:ring-brand-500/60 focus:border-brand-500/60"
                            />
                          ) : (
                            step.instruction && (
                              <p className="text-surface-300 text-xs leading-relaxed whitespace-pre-wrap break-words pl-0.5">
                                {step.instruction}
                              </p>
                            )
                          )}
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
              {isEditingPlan ? (
                <p className="mt-1 text-[10px] text-surface-500 pr-1">
                  Rewrite any step&apos;s instructions or remove one entirely — your edits are sent exactly as you leave them when you hit Approve.
                </p>
              ) : (
                isScrollMode && displaySteps.length > 3 && (
                  <p className="mt-1 text-[10px] text-surface-500 text-right pr-1">
                    ↕ Scroll inside box to see all {displaySteps.length} steps
                  </p>
                )
              )}
            </div>
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
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={el.url} alt="" className="h-8 w-8 shrink-0 rounded-md object-cover" />
                  )}
                </div>
              ))}
            </div>
          )}
          {m.formatBadge && (
            <div className="mb-1.5 flex items-center">
              <span className="inline-flex items-center gap-1 rounded-md bg-brand-500/20 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-brand-300 border border-brand-500/30">
                📐 {m.formatBadge}
              </span>
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
                // eslint-disable-next-line @next/next/no-img-element
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
              {m.options.map((opt) => (
                <button
                  key={opt.id}
                  onClick={() => handlePickOption?.(opt, canEditPlan && opt.id === "approve" ? draftSteps : undefined)}
                  disabled={loading || (isEditingPlan && opt.id === "approve")}
                  title={isEditingPlan && opt.id === "approve" ? "Finish editing (✓ Done) before approving" : undefined}
                  className={
                    "flex items-center justify-between rounded-xl border px-4 py-2.5 text-left text-sm disabled:opacity-50 transition-all hover:scale-[1.01] " +
                    (m.gateStage === "motion_pending" && opt.id === "approve"
                      ? "border-red-900/50 bg-red-900/20 hover:bg-red-900/40 text-red-200"
                      : "border-surface-700/50 bg-surface-800/80 hover:bg-surface-700 text-surface-200")
                  }
                >
                  <span className="flex items-center gap-3">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-surface-700/50 text-xs font-medium text-surface-400">
                      {opt.label}
                    </span>
                    {opt.description}
                  </span>
                </button>
              ))}
              {m.allowFreeText && (
                <button
                  onClick={() => {
                    if (handleOther) {
                      handleOther();
                    } else {
                      handlePickOption?.({ id: "free_text", label: "✎", description: "Answer below..." });
                    }
                  }}
                  disabled={loading}
                  className="flex items-center gap-3 rounded-xl border border-dashed border-surface-700 bg-transparent px-4 py-2.5 text-left text-sm hover:bg-surface-800/50 disabled:opacity-50 transition-all text-surface-400 hover:text-surface-300"
                >
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-surface-800 text-xs font-medium">
                    ✎
                  </span>
                  Type your own answer...
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
