"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import CanvasEngine, { CanvasTile } from "@/components/canvas/CanvasEngine";
import {
  CanvasElement,
  approveEdit,
  assetUrl,
  commentOnElement,
  directEdit,
  getCanvasState,
  listVersions,
  redoElement,
  regenerateElement,
  rejectEdit,
  undoElement,
  uploadAsset,
} from "@/lib/canvas";
import { ApiError } from "@/lib/http";

function toTiles(elements: CanvasElement[], versionCounts: Map<string, number>): CanvasTile[] {
  return elements
    .filter((el): el is CanvasElement & { storage_ref: string } => Boolean(el.storage_ref))
    .map((el) => ({
      id: el.id,
      kind: el.element_type === "video" ? "video" : "image",
      url: assetUrl(el.storage_ref),
      hasComment: Boolean(el.last_comment),
      version: el.version,
      versionCount: versionCounts.get(el.id),
      complianceStatus: el.compliance_status,
    }));
}

export interface ReferencedElement {
  id: string;
  kind: "image" | "video";
  url: string;
}

interface CanvasViewProps {
  sessionId: string | null;
  /** Bumped by the parent whenever a chat turn produces a new element — this view has no other
   * way to know a generation finished elsewhere, since it only owns the canvas, not the chat. */
  refreshSignal?: number;
  /** Which element (if any) is currently "referenced in chat" — owned by the parent (`page.tsx`)
   * since both this view (to highlight the tile) and `ChatPanel` (to show the chip and send it)
   * need it. */
  referencedElementId?: string | null;
  onReferenceElement?: (el: ReferencedElement | null) => void;
}

export default function CanvasView({
  sessionId,
  refreshSignal,
  referencedElementId,
  onReferenceElement,
}: CanvasViewProps) {
  const [elements, setElements] = useState<CanvasElement[]>([]);
  const [versionCounts, setVersionCounts] = useState<Map<string, number>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [showElements, setShowElements] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const directEditTargetRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    // No session yet (the user hasn't sent a first message) — a real empty infinite canvas, not
    // an error. Nothing to fetch, nothing to render.
    if (!sessionId) return;
    try {
      const state = await getCanvasState(sessionId);
      setElements(state.elements);
      setError(null);

      // Real version history (Memory.md: surfaced on the tile itself now) — only worth a real
      // fetch for elements that could possibly have more than one version; a fresh element at
      // v1 has nothing to show, so this never spends a request on the common case.
      const withHistory = state.elements.filter((el) => el.version > 1);
      const counts = await Promise.all(
        withHistory.map(async (el) => {
          try {
            const versions = await listVersions(el.id);
            return [el.id, versions.length] as const;
          } catch {
            return [el.id, 0] as const; // an honest "unknown," never a fabricated count
          }
        }),
      );
      setVersionCounts(new Map(counts));
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message} (HTTP ${err.status})` : "Could not load the canvas.");
    }
  }, [sessionId]);

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refreshSignal is a deliberate,
    // externally-bumped trigger, not a value `refresh` itself reads.
  }, [refresh, refreshSignal]);

  // The real compliance gate now runs as a genuine background task (session_service.py) — it
  // finishes after this turn's own HTTP response already returned, so there's no other signal
  // telling this view "QA just finished" the way `refreshSignal` announces "a generation just
  // finished." A short, bounded poll is the honest way to catch that: only runs while something
  // visible is actually still "running", stops itself the moment nothing is, never an indefinite
  // background poll.
  useEffect(() => {
    if (!elements.some((el) => el.compliance_status === "running")) return;
    const interval = setInterval(refresh, 2500);
    return () => clearInterval(interval);
  }, [elements, refresh]);

  async function withBusy(elementId: string, fn: () => Promise<CanvasElement>) {
    setBusyId(elementId);
    try {
      await fn();
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message} (HTTP ${err.status})` : "That action failed.");
    } finally {
      setBusyId(null);
    }
  }

  function handleRegenerate(el: CanvasElement) {
    const instruction = window.prompt("Describe the change for a targeted regenerate (same specialist):", "");
    if (instruction === null) return;
    withBusy(el.id, () => regenerateElement(el.id, instruction || undefined));
  }

  function handleComment(el: CanvasElement) {
    const text = window.prompt("Leave a comment (may route to a different specialist):", "");
    if (!text) return;
    withBusy(el.id, () => commentOnElement(el.id, text));
  }

  function handleUndo(elementId: string) {
    withBusy(elementId, () => undoElement(elementId));
  }

  function handleRedo(elementId: string) {
    withBusy(elementId, () => redoElement(elementId));
  }

  function handleSelectTile(tileId: string) {
    // Toggle: clicking the already-referenced tile again clears it, matching the chat chip's own
    // "×" affordance rather than being a one-way-only selection.
    if (tileId === referencedElementId) {
      onReferenceElement?.(null);
      return;
    }
    const el = elements.find((e) => e.id === tileId);
    if (!el?.storage_ref) return;
    onReferenceElement?.({
      id: el.id,
      kind: el.element_type === "video" ? "video" : "image",
      url: assetUrl(el.storage_ref),
    });
  }

  function handleDirectEditClick(el: CanvasElement) {
    directEditTargetRef.current = el.id;
    fileInputRef.current?.click();
  }

  async function handleFileChosen(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    const targetId = directEditTargetRef.current;
    e.target.value = "";
    if (!file || !targetId) return;
    setBusyId(targetId);
    try {
      const uploaded = await uploadAsset(file);
      await directEdit(targetId, uploaded.storage_ref);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message} (HTTP ${err.status})` : "Direct edit failed.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="relative h-full w-full">
      <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileChosen} />
      {error && (
        <div className="absolute inset-x-0 top-0 z-20 bg-red-100 px-4 py-2 text-sm text-red-800">{error}</div>
      )}
      <CanvasEngine
        tiles={toTiles(elements, versionCounts)}
        onUndo={handleUndo}
        onRedo={handleRedo}
        onSelectTile={handleSelectTile}
        referencedTileId={referencedElementId}
      />
      {/* A collapsed-by-default drawer, not an always-visible bar — the canvas is the point, per
       * the user's explicit "infinite canvas with a chat" direction; the element inspector should
       * not compete with it for screen space by default. */}
      <button
        onClick={() => setShowElements((s) => !s)}
        className="pointer-events-auto absolute right-4 top-4 z-20 rounded-full bg-neutral-900 px-4 py-2 text-sm font-medium text-white shadow-lg"
      >
        {showElements ? "Hide" : "Elements"} ({elements.length})
      </button>
      {showElements && (
        <div className="pointer-events-auto absolute right-4 top-16 z-20 max-h-[50vh] w-80 overflow-y-auto rounded-2xl border border-neutral-200 bg-white p-3 shadow-2xl">
        {elements.length === 0 && <p className="text-sm text-neutral-500">No elements yet for this session.</p>}
        <div className="flex flex-col gap-2">
          {elements.map((el) => (
            <div key={el.id} className="flex items-center justify-between gap-2 rounded border border-neutral-200 p-2 text-sm">
              <div>
                <span className="font-medium">{el.element_type}</span>{" "}
                <span className="text-neutral-500">
                  by {el.produced_by_specialist} · v{el.version}
                </span>
                {el.pending_storage_ref && (
                  <span className="ml-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">pending approval</span>
                )}
                {el.compliance_status === "running" && (
                  <span className="ml-2 rounded bg-neutral-200 px-2 py-0.5 text-xs text-neutral-700">running QA…</span>
                )}
                {el.compliance_status === "failed" && (
                  <span className="ml-2 rounded bg-red-100 px-2 py-0.5 text-xs text-red-800">✕ failed QA</span>
                )}
              </div>
              <div className="flex gap-1">
                {el.pending_storage_ref ? (
                  <>
                    <button
                      onClick={() => withBusy(el.id, () => approveEdit(el.id))}
                      disabled={busyId === el.id}
                      className="rounded bg-neutral-900 px-2 py-1 text-xs text-white disabled:opacity-50"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => withBusy(el.id, () => rejectEdit(el.id))}
                      disabled={busyId === el.id}
                      className="rounded border border-neutral-300 px-2 py-1 text-xs disabled:opacity-50"
                    >
                      Reject
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={() => handleRegenerate(el)}
                      disabled={busyId === el.id}
                      className="rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 disabled:opacity-50"
                    >
                      Regenerate
                    </button>
                    <button
                      onClick={() => handleComment(el)}
                      disabled={busyId === el.id}
                      className="rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 disabled:opacity-50"
                    >
                      Comment
                    </button>
                    {el.element_type === "image" && (
                      <button
                        onClick={() => handleDirectEditClick(el)}
                        disabled={busyId === el.id}
                        className="rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 disabled:opacity-50"
                      >
                        Direct edit
                      </button>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
        </div>
      )}
    </div>
  );
}
