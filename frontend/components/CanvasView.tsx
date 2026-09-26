"use client";

import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import CanvasEngine, { CanvasEngineHandle, CanvasTile } from "@/components/canvas/CanvasEngine";
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
  uploadAndPlaceElement,
  uploadAsset,
} from "@/lib/canvas";
import { ApiError } from "@/lib/http";

/** Count of recorded versions AND the highest version number actually recorded — kept separate
 * because they can genuinely differ (an element edited via chat before a real backend fix landed
 * can have recorded versions like {1, 5}: count=2, but the latest is numbered 5, not 2). Conflating
 * them was a real, live-found bug: the redo button compared the current version NUMBER against the
 * COUNT, which permanently disabled redo for exactly these gappy elements. */
interface VersionInfo {
  count: number;
  max: number;
}

/** "audio" added 2026-09-22 alongside the real "full_audio" orchestrator route and intermediate-
 * artifact surfacing (voiceover tracks) — the one place `element_type` gets mapped to a render
 * "kind", reused everywhere else here rather than re-deriving it (a real, live-found bug this
 * fixes: the two-way `"video" ? "video" : "image"` ternary silently mapped every audio element to
 * "image", which `CanvasEngine` then tried to render as an `<img>`). "text" added 2026-09-22
 * alongside real shot-list/scene-description/creative-brief cards — the reference product's own
 * canvas shows exactly this kind of text card next to the generated tiles; this app already
 * computed all of this real text, it just never became a visible element before. */
function elementKind(elementType: string): "image" | "video" | "audio" | "text" {
  if (elementType === "video") return "video";
  if (elementType === "audio") return "audio";
  if (elementType === "text") return "text";
  return "image";
}

/** Canvas Grouping (2026-09-25, revised same day: a workflow IS one campaign — grouping is by
 * real Product DNA instead) — groups the Elements Drawer's real elements by their real
 * `product_id`, same "Unassigned" bucket concept `CanvasEngine.tsx`'s own canvas-frame grouping
 * uses (elements with no real product never fabricate one). Order follows first appearance in
 * `elements` (already creation-ordered), so groups don't jump around on refetch. */
function groupElementsByProduct(
  elements: CanvasElement[],
): { key: string; label: string; items: CanvasElement[] }[] {
  const order: string[] = [];
  const byKey = new Map<string, { key: string; label: string; items: CanvasElement[] }>();
  for (const el of elements) {
    const key = el.product_id || "__unassigned__";
    if (!byKey.has(key)) {
      byKey.set(key, {
        key,
        label: el.product_id ? (el.product_name || "Untitled product") : "Unassigned",
        items: [],
      });
      order.push(key);
    }
    byKey.get(key)!.items.push(el);
  }
  return order.map((k) => byKey.get(k)!);
}

/** A real, small, local add/edit-free collapsible section (2026-09-25) — no reusable accordion
 * component existed anywhere in this frontend before this (confirmed live audit), so this stays
 * local to the one place that needs it rather than becoming a premature shared component. */
function Accordion({
  title, count, defaultOpen = true, children,
}: { title: string; count: number; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-xl border border-surface-700/50 bg-surface-800/20">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-3 py-2 text-xs font-semibold text-surface-300 hover:text-white transition-colors"
      >
        <span>{open ? "▾" : "▸"} {title} ({count})</span>
      </button>
      {open && <div className="flex flex-col gap-3 p-3 pt-0">{children}</div>}
    </div>
  );
}

function toTiles(elements: CanvasElement[], versionInfo: Map<string, VersionInfo>): CanvasTile[] {
  return elements
    .map((el) => ({
      id: el.id,
      kind: elementKind(el.element_type),
      url: el.storage_ref ? assetUrl(el.storage_ref) : "",
      content: el.text_content ?? undefined,
      hasComment: Boolean(el.last_comment),
      version: el.version,
      versionCount: versionInfo.get(el.id)?.count,
      maxVersion: versionInfo.get(el.id)?.max,
      complianceStatus: el.compliance_status,
      createdAt: el.created_at,
      producedBy: el.produced_by_specialist,
      description: el.description,
      alignmentWarning: el.alignment_warning ?? undefined,
      productId: el.product_id,
      productName: el.product_name,
      parentElementId: el.parent_element_id,
    }));
}

export interface ReferencedElement {
  id: string;
  kind: "image" | "video" | "audio" | "text";
  url: string;
  // A real, short, honest label for what's being referenced (2026-09-22, per an explicit user
  // ask: "label everything properly and relative to what's generated") — `CanvasElement.description`
  // — so the chat's "re: this ___" chip says what it actually is, not just its generic kind.
  description?: string | null;
}

interface CanvasViewProps {
  sessionId: string | null;
  /** Bumped by the parent whenever a chat turn produces a new element — this view has no other
   * way to know a generation finished elsewhere, since it only owns the canvas, not the chat. */
  refreshSignal?: number;
  /** Which element (if any) is currently "referenced in chat" — owned by the parent (`page.tsx`)
   * since both this view (to highlight the tile) and `ChatPanel` (to show the chip and send it)
   * need it. */
  referencedElementIds?: string[];
  onReferenceElements?: (elements: ReferencedElement[]) => void;
  /** The canvas right-click menu's "New Image"/"New Video"/"New Audio" (2026-09-22) — a real
   * GENERATION request, not an upload, so this view hands it to the parent rather than handling
   * it itself: it has no chat/turn machinery of its own (that's `ChatPanel`'s job), same reason
   * `onReferenceElement` is already a callback rather than something this view owns end-to-end.
   * "audio" reaches a real backend route (orchestrator.py's "full_audio", added 2026-09-22) —
   * Sound Designer alone, spoken voiceover only, never music (its own honest, disclosed limit). */
  onRequestGenerate?: (kind: "image" | "video" | "audio") => void;
  /** A real loading silhouette (2026-09-22) — `null` kind is a genuine "working, kind not known
   * yet" state (e.g. before `route_decided` arrives, or a `direct_fix` whose real output kind
   * isn't decided until it actually runs), never a guessed icon. `null`/absent entirely means no
   * turn is in flight right now. */
  pendingGeneration?: { kind: "image" | "video" | "audio" | "text" | null } | null;
}

export default function CanvasView({
  sessionId,
  refreshSignal,
  referencedElementIds = [],
  onReferenceElements,
  onRequestGenerate,
  pendingGeneration,
}: CanvasViewProps) {
  const [elements, setElements] = useState<CanvasElement[]>([]);
  const [versionInfo, setVersionInfo] = useState<Map<string, VersionInfo>>(new Map());
  // Campaign Grouping (2026-09-25, CAMPAIGN_GROUPING_TASKS.md) — lets the Elements Drawer pan the
  // canvas to a specific tile without CanvasEngine needing any drawer-related awareness itself.
  const canvasEngineRef = useRef<CanvasEngineHandle>(null);
  // Elements confirmed (by a real listVersions fetch) to have more than one version — a ref, not
  // state, so `refresh` can read the latest value without needing it in its own dependency array
  // (adding `versionInfo` there would recreate `refresh` on every fetch, since `setVersionInfo`
  // always produces a new Map reference, which would re-trigger the effect that calls `refresh`
  // in an infinite loop). Kept across an undo back to v1, so that element's real history — and
  // therefore its redo control — isn't lost the moment its current version drops back to 1.
  const knownMultiVersionRef = useRef<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [showElements, setShowElements] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const directEditTargetRef = useRef<string | null>(null);

  // Right-click "New Image/Video (generate), Upload Media, Paste (upload)" menu (2026-09-22) — a
  // second, plain <input type="file"> rather than reusing `fileInputRef` above: that one is wired
  // to the direct-edit flow (a specific `directEditTargetRef` element it replaces), while this one
  // always creates a brand-new element, so sharing a ref between the two would make each
  // `onChange` handler guess which flow actually triggered it.
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const newElementInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [contextMenu]);

  // A real Cmd/Ctrl+V paste anywhere on this view — the native browser `paste` event, not the
  // permissions-gated `navigator.clipboard.read()` API, so it works with no extra prompt the
  // instant the canvas has focus. The context menu's own "Paste" item (below) is a second, more
  // explicit entry point using the Clipboard API instead, since a genuine click DOES carry the
  // user-activation that API requires.
  useEffect(() => {
    function onPaste(e: ClipboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target && target.closest("input, textarea, [contenteditable=true]")) return; // real text paste elsewhere on the page — not ours to intercept
      const file = Array.from(e.clipboardData?.items ?? [])
        .find((item) => item.kind === "file")
        ?.getAsFile();
      if (!file) return;
      e.preventDefault();
      void handleUploadFile(file);
    }
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `handleUploadFile` closes over
    // `sessionId`/`refresh`, both stable for the lifetime of one mounted session.
  }, [sessionId]);

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
      // v1 has nothing to show, so this never spends a request on the common case. Also keep
      // fetching for any element ALREADY confirmed (via `knownMultiVersionRef`) to have more than
      // one version — real, live-found bug (2026-09-21): undoing back to v1 dropped an element
      // out of this filter entirely, so its versionCount went back to `undefined` and the whole
      // undo/redo control vanished from the tile, even though its real history (e.g. v1 and v5)
      // still existed — making undo a one-way trip with no way to redo.
      const withHistory = state.elements.filter(
        (el) => el.version > 1 || knownMultiVersionRef.current.has(el.id),
      );
      const infos = await Promise.all(
        withHistory.map(async (el) => {
          try {
            const versions = await listVersions(el.id);
            const max = versions.reduce((m, v) => Math.max(m, v.version), 0);
            return [el.id, { count: versions.length, max }] as const;
          } catch {
            return [el.id, { count: 0, max: 0 }] as const; // an honest "unknown," never fabricated
          }
        }),
      );
      for (const [id, info] of infos) {
        if (info.count > 1) knownMultiVersionRef.current.add(id);
      }
      setVersionInfo(new Map(infos));
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
    if (referencedElementIds?.includes(tileId)) {
      // Deselect if already selected
      const currentlyReferenced = elements
        .filter(el => referencedElementIds.includes(el.id) && el.id !== tileId)
        .map(el => ({
          id: el.id,
          kind: elementKind(el.element_type),
          url: el.storage_ref ? assetUrl(el.storage_ref) : "",
          description: el.description,
        }));
      onReferenceElements?.(currentlyReferenced);
      return;
    }

    // Otherwise, append to existing selection
    const el = elements.find((e) => e.id === tileId);
    if (!el) return;

    const currentlyReferenced = elements
      .filter(e => referencedElementIds?.includes(e.id))
      .map(e => ({
        id: e.id,
        kind: elementKind(e.element_type),
        url: e.storage_ref ? assetUrl(e.storage_ref) : "",
        description: e.description,
      }));

    onReferenceElements?.([...currentlyReferenced, {
      id: el.id,
      kind: elementKind(el.element_type),
      url: el.storage_ref ? assetUrl(el.storage_ref) : "",
      description: el.description,
    }]);
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

  /** The one real action every UPLOAD-based right-click menu item (Upload Media, Paste) reduces
   * to — upload the bytes, place them as a brand-new canvas element, refresh. "New Image"/
   * "New Video" are GENERATION requests instead (`onRequestGenerate`), not this path. */
  async function handleUploadFile(file: File | Blob) {
    if (!sessionId) return;
    setUploading(true);
    try {
      await uploadAndPlaceElement(sessionId, file);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message} (HTTP ${err.status})` : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  /** Opens the OS file picker filtered to one accept string — "Upload Media" accepts all three
   * kinds; "New Image"/"New Video" (above) are real GENERATION requests instead, routed through
   * `onRequestGenerate`, not this upload path at all. */
  function handleNewElementClick(accept: string) {
    setContextMenu(null);
    if (newElementInputRef.current) newElementInputRef.current.accept = accept;
    newElementInputRef.current?.click();
  }

  async function handleNewElementChosen(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    await handleUploadFile(file);
  }

  /** The context menu's own "Paste" item — a genuine click carries the user-activation the
   * permissions-gated Clipboard API requires, unlike the global `paste` event listener above
   * (which needs no such gesture but only fires on an actual Cmd/Ctrl+V). Surfaces a clear error
   * rather than failing silently if the browser blocks it (e.g. no image currently on the
   * clipboard, or the permission was denied). */
  async function handlePasteFromMenu() {
    setContextMenu(null);
    try {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        const imageType = item.types.find((t) => t.startsWith("image/"));
        if (imageType) {
          await handleUploadFile(await item.getType(imageType));
          return;
        }
      }
      setError("No image found on the clipboard.");
    } catch {
      setError("Couldn't read the clipboard — try Cmd/Ctrl+V instead.");
    }
  }

  return (
    <div className="relative h-full w-full">
      <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileChosen} />
      <input ref={newElementInputRef} type="file" className="hidden" onChange={handleNewElementChosen} />
      {error && (
        <div className="absolute inset-x-0 top-0 z-20 bg-red-900/80 px-4 py-2 text-sm text-red-200 backdrop-blur-md border-b border-red-700/50">{error}</div>
      )}
      {uploading && (
        <div className="absolute inset-x-0 top-0 z-20 bg-surface-900/80 px-4 py-2 text-center text-sm text-surface-200 backdrop-blur-md border-b border-surface-700/50">
          Uploading…
        </div>
      )}
      <CanvasEngine
        ref={canvasEngineRef}
        tiles={toTiles(elements, versionInfo)}
        onUndo={handleUndo}
        onRedo={handleRedo}
        onSelectTile={handleSelectTile}
        referencedTileIds={referencedElementIds}
        onContextMenu={(at) => setContextMenu({ x: at.clientX, y: at.clientY })}
        pendingGeneration={pendingGeneration}
        sessionId={sessionId}
      />
      {contextMenu && (
        <div
          className="absolute z-30 w-48 overflow-hidden rounded-xl border border-surface-700/50 bg-surface-900/80 py-1 text-sm shadow-2xl backdrop-blur-xl animate-fade-in-up"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          // Stop this menu's own pointerdown from immediately re-triggering the window listener
          // that closes it (the listener above runs in the bubble phase after this).
          onPointerDown={(e) => e.stopPropagation()}
        >
          <button
            onClick={() => {
              setContextMenu(null);
              onRequestGenerate?.("image");
            }}
            className="flex w-full items-center px-4 py-2 text-left hover:bg-surface-800/80 text-surface-200 transition-colors"
          >
            New Image
          </button>
          <button
            onClick={() => {
              setContextMenu(null);
              onRequestGenerate?.("video");
            }}
            className="flex w-full items-center px-4 py-2 text-left hover:bg-surface-800/80 text-surface-200 transition-colors"
          >
            New Video
          </button>
          <button
            onClick={() => {
              setContextMenu(null);
              onRequestGenerate?.("audio");
            }}
            className="flex w-full items-center px-4 py-2 text-left hover:bg-surface-800/80 text-surface-200 transition-colors"
          >
            New Audio
          </button>
          <div className="my-1 border-t border-surface-700/50" />
          <button
            onClick={() => handleNewElementClick("image/*,video/*,audio/*")}
            className="flex w-full items-center px-4 py-2 text-left hover:bg-surface-800/80 text-surface-200 transition-colors"
          >
            Upload Media
          </button>
          <button onClick={handlePasteFromMenu} className="flex w-full items-center px-4 py-2 text-left hover:bg-surface-800/80 text-surface-200 transition-colors">
            Paste
          </button>
        </div>
      )}
      {/* A collapsed-by-default drawer, not an always-visible bar — the canvas is the point, per
       * the user's explicit "infinite canvas with a chat" direction; the element inspector should
       * not compete with it for screen space by default. */}
      <button
        onClick={() => setShowElements((s) => !s)}
        className="pointer-events-auto absolute right-6 top-6 z-20 rounded-full bg-surface-900/60 border border-surface-700/50 px-4 py-2 text-sm font-medium text-surface-200 shadow-xl backdrop-blur-xl transition-all hover:bg-surface-800/80 hover:text-white hover:-translate-y-0.5 animate-fade-in-up"
      >
        {showElements ? "Hide" : "Elements"} ({elements.length})
      </button>
      {showElements && (
        <div className="pointer-events-auto absolute right-6 top-20 z-20 max-h-[50vh] w-80 overflow-y-auto rounded-2xl border border-surface-700/50 bg-surface-900/60 p-4 shadow-2xl backdrop-blur-xl animate-fade-in-up">
        {elements.length === 0 && <p className="text-sm text-surface-400">No elements yet for this session.</p>}
        {/* Canvas Grouping (2026-09-25) — collapsible accordions by real product, replacing
         * the old flat list; a session's elements are no longer indistinguishable from each other
         * once there's more than one product running through the same workflow. */}
        <div className="flex flex-col gap-2">
          {groupElementsByProduct(elements).map((group) => (
            <Accordion key={group.key} title={group.label} count={group.items.length}>
              {group.items.map((el) => (
            <div key={el.id} className="flex items-center justify-between gap-2 rounded-xl border border-surface-700/50 bg-surface-800/30 p-3 text-sm transition-colors hover:bg-surface-800/50">
              {/* Campaign Grouping (2026-09-25) — click the info side to pan the canvas to this
               * tile; kept off the button side so it never fights with Regenerate/Comment/etc. */}
              <div className="cursor-pointer" onClick={() => canvasEngineRef.current?.focusTile(el.id)} title="Click to locate on canvas">
                <span className="font-medium capitalize text-surface-200">{el.element_type}</span>{" "}
                <span className="text-surface-500">
                  by {el.produced_by_specialist} · v{el.version}
                </span>
                {/* A real description, not a fabricated one (2026-09-22, per an explicit user ask:
                    "label everything properly and relative to what's generated") — the element
                    type alone doesn't distinguish one "image" from another in a long session with
                    many of them; this is the backend's own real recorded content for it. */}
                {el.description && (
                  <p className="mt-1 line-clamp-2 text-xs text-surface-400" title={el.description}>
                    {el.description}
                  </p>
                )}
                {el.pending_storage_ref && (
                  <span className="ml-2 rounded bg-amber-900/50 border border-amber-700/50 px-2 py-0.5 text-xs text-amber-200">pending approval</span>
                )}
                {el.compliance_status === "running" && (
                  <span className="ml-2 rounded bg-surface-800 border border-surface-600/50 px-2 py-0.5 text-xs text-surface-300">running QA…</span>
                )}
                {el.compliance_status === "failed" && (
                  <span className="ml-2 rounded bg-red-900/50 border border-red-700/50 px-2 py-0.5 text-xs text-red-200">✕ failed QA</span>
                )}
              </div>
              <div className="flex gap-1.5 flex-col sm:flex-row">
                {el.pending_storage_ref ? (
                  <>
                    <button
                      onClick={() => withBusy(el.id, () => approveEdit(el.id))}
                      disabled={busyId === el.id}
                      className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs text-white transition-colors hover:bg-brand-500 disabled:opacity-50"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => withBusy(el.id, () => rejectEdit(el.id))}
                      disabled={busyId === el.id}
                      className="rounded-lg border border-surface-600/50 px-3 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700/50 disabled:opacity-50"
                    >
                      Reject
                    </button>
                  </>
                ) : (
                  <div className="flex gap-1.5 flex-wrap justify-end">
                    <button
                      onClick={() => handleRegenerate(el)}
                      disabled={busyId === el.id}
                      className="rounded-lg border border-surface-600/50 px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700/50 hover:text-white disabled:opacity-50"
                    >
                      Regenerate
                    </button>
                    <button
                      onClick={() => handleComment(el)}
                      disabled={busyId === el.id}
                      className="rounded-lg border border-surface-600/50 px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700/50 hover:text-white disabled:opacity-50"
                    >
                      Comment
                    </button>
                    {el.element_type === "image" && (
                      <button
                        onClick={() => handleDirectEditClick(el)}
                        disabled={busyId === el.id}
                        className="rounded-lg border border-surface-600/50 px-2.5 py-1.5 text-xs text-surface-300 transition-colors hover:bg-surface-700/50 hover:text-white disabled:opacity-50"
                      >
                        Edit
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
              ))}
            </Accordion>
          ))}
        </div>
        </div>
      )}
    </div>
  );
}
