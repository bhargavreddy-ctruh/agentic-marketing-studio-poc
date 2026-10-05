"use client";

/**
 * THE canvas engine — no third-party canvas library, by deliberate choice (Memory.md: the user's
 * explicit ask to avoid any future licensing question after researching tldraw's terms). Pan/zoom
 * mechanics are ported from the user's own prior project
 * (`~/Downloads/luma_agents/client/src/components/Canvas/InfiniteCanvas.tsx` +
 * `utils/camera.ts`) — their own code, real and already working, zero licensing concerns since
 * there's no third-party dependency at all. The freehand drawing layer is new here (that prior
 * project never had one — grepped for it directly, confirmed absent).
 *
 * `CanvasView.tsx` (and anything else) depends only on `CanvasEngineProps` below — the same
 * "one file owns the mechanism, everything else depends on an interface" isolation this project's
 * backend already applies to every vendor provider (Rules.md section 1).
 */
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Camera, Viewport, fitViewport } from "./camera";
import type { SpecialistAgent } from "../AgentHUD";
import { canvasTileImageUrl } from "@/lib/http";

export interface CanvasTile {
  id: string;
  /** "audio" added 2026-09-22 alongside the real "full_audio" orchestrator route and intermediate-
   * artifact surfacing — a real, distinct tile kind (an `<audio controls>` strip), not folded into
   * "image": a real, live-found bug caught before this ever reached a user — every audio element
   * used to fall through the `kind === "video" ? "video" : "image"` mapping straight to "image",
   * so the canvas tried to render raw WAV bytes inside an `<img>` tag (a broken-image icon, not a
   * playable clip). */
  /** "text" added 2026-09-22 — a real shot-list/scene-description/creative-brief CARD, matching
   * the reference product's own canvas (a labeled text block next to the generated tiles); this
   * app already computed all of this real text, it just never became a visible element before. */
  kind: "image" | "video" | "audio" | "text";
  url: string;
  /** Real text content for `kind: "text"` tiles — there's no `url`/asset at all for these (the
   * text itself IS the content), so `url` is just an empty string for them; render from here. */
  content?: string;
  /** Real, already-stored data (comment_service.py) — renders a small locked-to-the-tile badge,
   * never a separate tracked position (Memory.md: the badge is a DOM child of the tile itself, so
   * it moves for free whenever the tile is dragged). */
  hasComment?: boolean;
  /** Real version history (versioning_service.py) — when `versionCount` is more than 1, the tile
   * shows a real "vX/Y" strip wired to the actual undo/redo endpoints, not a cosmetic counter.
   * `versionCount` is only a display count (how many versions exist) — NOT the same thing as the
   * highest version NUMBER, which `maxVersion` carries separately. Real, live-found bug
   * (2026-09-21): an element edited via chat before a backend fix landed can have real recorded
   * versions like {1, 5} — count=2, but the latest version is numbered 5, not 2. The redo button
   * used to compare `version >= versionCount` (5 >= 2 → permanently "disabled", even sitting at
   * v1 with a real v5 to redo to) instead of comparing against the actual highest version number. */
  version?: number;
  versionCount?: number;
  maxVersion?: number;
  /** The real compliance gate's status (compliance_gate.py), run as a real background task right
   * after generation — "running" shows a small pulsing badge while QA is genuinely in progress,
   * "failed" a red cross. "passed"/"disabled" (COMPLIANCE_QA_ENABLED=false)/undefined render
   * nothing — "disabled" is an honest "not checked", never shown as if it passed. */
  complianceStatus?: "running" | "passed" | "failed" | "disabled";
  /** ISO timestamp (`CanvasElement.created_at`) — the real, already-stored signal timeline
   * grouping (2026-09-22) clusters by, not a synthetic client-side counter. */
  createdAt?: string;
  /** `produced_by_specialist` — folded into a group's label alongside its time range, echoing the
   * reference screenshot's "Campaign Brief / Keyframes / Video Clips" stage labels with data this
   * app actually has, rather than inventing a stage taxonomy that doesn't exist in the backend. */
  producedBy?: string;
  /** A real, short, honest caption of what this tile actually IS (2026-09-22, per an explicit user
   * ask: "label everything properly and relative to what's generated") — `CanvasElement.description`
   * (the real image/motion prompt, voiceover line, etc. the backend recorded), rendered as a small
   * caption strip under the tile so a long session's many tiles are distinguishable at a glance
   * without opening each one. Undefined/empty renders no caption — never a fabricated placeholder. */
  description?: string | null;
  /** Warning message injected by Laya decision model if the output diverges from user intent */
  alignmentWarning?: string;
  /** Product Grouping (2026-09-25, ported from `poc/frontend/components/canvas/CanvasEngine.tsx`
   * — replaces the time-gap heuristic below outright, per that session's explicit user decision:
   * a workflow IS one campaign; grouping is by real Product DNA instead) — which real product this
   * tile belongs to (null/undefined = unassigned, grouped into one flat, still-framed-but-muted
   * bucket), and which tile (if any) it was generated from — rendered as a small lineage badge.
   * Real backend fields (`CanvasElement.product_id`/`product_name`/`parent_element_id`), never
   * client-invented. */
  productId?: string | null;
  productName?: string | null;
  parentElementId?: string | null;
}

export interface CanvasEngineProps {
  tiles: CanvasTile[];
  onUndo?: (tileId: string) => void;
  onRedo?: (tileId: string) => void;
  /** Fires on a genuine click (not a drag) — "reference this element in chat," distinguished from
   * dragging by the same real-movement threshold the pan gesture already uses for itself. */
  onSelectTile?: (tileId: string) => void;
  referencedTileIds?: string[];
  /** A real right-click on empty canvas — the owner (`CanvasView`) renders its own context menu
   * overlay from this; this engine only reports WHERE, since it has no idea what actions (upload,
   * paste, etc.) exist upstream, keeping this file's own "just the mechanism" scope intact. */
  onContextMenu?: (at: { clientX: number; clientY: number }) => void;
  /** A real loading silhouette (2026-09-22, per an explicit user ask) — driven by real SSE events
   * (`page.tsx`'s own `handleTurnEvent`), not a fake spinner. `kind: null` is a genuine "working,
   * not decided yet" state (before `route_decided` arrives, or a `direct_fix` whose real output
   * kind isn't known until it actually runs) — shown honestly as a generic placeholder, never a
   * guessed icon. Deliberately rendered OUTSIDE `laidOut`/the timeline-grouping mechanism (never
   * wrapped in a group's dashed boundary) — it isn't a real, generated element yet. */
  pendingGeneration?: { kind: "image" | "video" | "audio" | "text" | null } | null;
  /** Active specialist currently executing a generation or review task */
  activeSpecialist?: { agent: SpecialistAgent; taskLabel: string; thinkingSnippet?: string } | null;
  /** Used to key localStorage position persistence — positions survive a page refresh and are
   * silently dropped only when the session itself no longer exists. Omitting it disables
   * persistence (no-op, safe) rather than mixing two sessions' positions under the same key. */
  sessionId?: string | null;
}

const CLICK_MOVE_THRESHOLD = 4; // px of real movement before a press counts as a drag, not a click

const GRID_SIZE = 28;
const TILE_SIZE = 320;
const TILE_GAP = 48;
const COLUMNS = 4;
// The drawing layer's own fixed world-space canvas — large enough for normal use; strokes outside
// this area are simply not capturable, an honest, disclosed limit of a first pass, not silently
// dropped in the middle of a real drawing.
const DRAW_CANVAS_SIZE = 6000;
const DRAW_ORIGIN = -DRAW_CANVAS_SIZE / 2;

// Product-based grouping (2026-09-25, ported from `poc/frontend/components/canvas/
// CanvasEngine.tsx` — replaces the earlier time-gap heuristic outright, per that session's
// explicit user decision: a workflow IS one campaign, so grouping is by real Product DNA instead
// of an ad-hoc time-proximity guess). Tiles group by their real, backend-assigned `productId` — a
// real `ProductProfileModel.id`, not a coincidence of when two unrelated things happened to be
// generated close together. Tiles with no `productId` (pre-migration elements, uploads, anything
// genuinely product-less) land in ONE flat "Unassigned" bucket — still framed, just visually muted
// so it's never mistaken for a real, named product.
const FRAME_PADDING = 32;
const FRAME_LABEL_HEIGHT = 28;
const RUN_ROW_GAP = 1; // extra empty row-band left between two runs, for visual separation + label room

interface TimelineGroup {
  id: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  tileIds: string[];
}

/** A "run" is now a real product group, not a time-proximity cluster — this is the ONE function
 * that decides run membership, reused for BOTH position assignment (`useTileLayout`) and frame
 * drawing, so a frame's boundary can never drift out of sync with where its own tiles actually
 * got laid out (the root cause of an earlier overlapping-frames bug this shape already fixed
 * once, preserved here). `productId: null` is the single flat "Unassigned" bucket — still framed,
 * just visually muted (see `computeTimelineFrames` below). */
interface Run {
  tileIds: string[];
  productId: string | null;
  productName: string | null;
}

function computeRuns(tiles: CanvasTile[]): Run[] {
  const order: string[] = [];
  const byKey = new Map<string, Run>();
  for (const tile of tiles) {
    const productId = tile.productId || null;
    const key = productId ?? "__unassigned__";
    let run = byKey.get(key);
    if (!run) {
      run = { tileIds: [], productId, productName: tile.productName || null };
      byKey.set(key, run);
      order.push(key);
    }
    run.tileIds.push(tile.id);
  }
  return order.map((key) => byKey.get(key)!);
}

/** Draws a real frame around every run, including the flat "Unassigned" bucket (per explicit user
 * report: two freshly-generated, product-less tiles got no boundary at all, which read as broken
 * rather than "not grouped yet"). A real product gets a 📦 label with its actual name; the
 * unassigned bucket gets an honest, visually distinct label instead of pretending to be a real
 * product group. Label is otherwise real item count/specialists — never fabricated. */
function computeTimelineFrames(
  tiles: CanvasTile[],
  laidOut: { tile: CanvasTile; pos: { x: number; y: number } }[],
): TimelineGroup[] {
  const posById = new Map(laidOut.map((l) => [l.tile.id, l.pos]));
  const tileById = new Map(tiles.map((t) => [t.id, t]));
  const frames: TimelineGroup[] = [];

  computeRuns(tiles).forEach((run) => {
    const runTiles = run.tileIds
      .map((id) => ({ tile: tileById.get(id), pos: posById.get(id) }))
      .filter((x): x is { tile: CanvasTile; pos: { x: number; y: number } } => Boolean(x.tile && x.pos));
    if (runTiles.length === 0) return;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const { pos } of runTiles) {
      minX = Math.min(minX, pos.x);
      minY = Math.min(minY, pos.y);
      maxX = Math.max(maxX, pos.x + TILE_SIZE);
      maxY = Math.max(maxY, pos.y + TILE_SIZE);
    }

    const specialists = Array.from(new Set(runTiles.map((r) => r.tile.producedBy).filter(Boolean)));
    const specialistLabel = specialists.length
      ? ` · ${specialists.slice(0, 2).join(", ")}${specialists.length > 2 ? "…" : ""}`
      : "";
    const itemsLabel = `${runTiles.length} item${runTiles.length === 1 ? "" : "s"}`;
    const label = run.productId
      ? `📦 ${run.productName || "Untitled product"} (${itemsLabel})${specialistLabel}`
      : `Unassigned (${itemsLabel})${specialistLabel}`;

    frames.push({
      id: run.productId ? `product-${run.productId}` : "unassigned",
      label,
      x: minX - FRAME_PADDING,
      y: minY - FRAME_PADDING - FRAME_LABEL_HEIGHT,
      w: maxX - minX + FRAME_PADDING * 2,
      h: maxY - minY + FRAME_PADDING * 2 + FRAME_LABEL_HEIGHT,
      tileIds: run.tileIds,
    });
  });

  return frames;
}

interface PointerState {
  x: number;
  y: number;
  startCam: Viewport;
  moved: boolean;
}

/** Assigns each real tile a stable grid position the first time it's seen, so tiles never jump
 * around on a later refresh just because the array was refetched in a different order. Also
 * exposes `movePosition` for real user dragging — a deliberate move a refetch must never undo,
 * which is exactly why positions live in a ref keyed by tile id rather than being recomputed.
 *
 * Real, live-found layout bug fixed here (2026-09-22): tiles used to be assigned into ONE
 * continuous grid purely by arrival order, with zero awareness of `computeRuns`' own group
 * boundaries — so a run's frame (drawn separately, around whatever bounding box its tiles
 * happened to land in) could overlap a NEIGHBORING run's tiles whenever a run boundary fell
 * mid-row. Now each run gets its own row-band (`RUN_ROW_GAP` empty rows between bands), so two
 * different runs' frames can never overlap, regardless of how many items either one has. */
function localKey(sessionId: string) {
  return `canvas-positions-${sessionId}`;
}

function loadSavedPositions(sessionId: string | null | undefined): Map<string, { x: number; y: number }> {
  if (!sessionId) return new Map();
  try {
    const raw = localStorage.getItem(localKey(sessionId));
    if (!raw) return new Map();
    const parsed: Record<string, { x: number; y: number }> = JSON.parse(raw);
    return new Map(Object.entries(parsed));
  } catch {
    return new Map();
  }
}

function savePositions(sessionId: string | null | undefined, map: Map<string, { x: number; y: number }>) {
  if (!sessionId) return;
  try {
    const obj: Record<string, { x: number; y: number }> = {};
    map.forEach((v, k) => { obj[k] = v; });
    localStorage.setItem(localKey(sessionId), JSON.stringify(obj));
  } catch {
    // localStorage quota exceeded — silently ignore, positions just won't survive the next refresh.
  }
}

function useTileLayout(tiles: CanvasTile[], sessionId?: string | null) {
  // Seed from localStorage on first mount so dragged positions survive a page refresh. Computed
  // as a plain `useMemo` value (not read off a ref) specifically so `initialNextRow` below can
  // derive from IT rather than from `positions.current` — real, live-found bug (2026-09-23,
  // `react-hooks/refs`): reading a ref during render, even one just created on this same render,
  // is against React's rules the same way writing one is (see the longer note on `layout` state
  // further down in this hook for the full story).
  const seededPositions = useMemo(() => loadSavedPositions(sessionId), [sessionId]);
  const positions = useRef(seededPositions);

  // Calculate the highest row currently occupied so new generations don't spawn at (0,0) and overlap old tiles.
  const initialNextRow = useMemo(() => {
    let maxRow = 0;
    seededPositions.forEach(pos => {
      // 600 is the hardcoded TILE_ROW_HEIGHT used below
      const row = Math.ceil(pos.y / 600);
      if (row > maxRow) maxRow = row;
    });
    return maxRow > 0 ? maxRow + 1 : 0;
  }, [seededPositions]);

  const nextRowRef = useRef(initialNextRow);

  // Real, live-found bug (2026-09-23 frontend audit, `react-hooks/refs` — the first time this
  // codebase ever had real lint coverage): this whole layout computation used to live in a
  // `useMemo`, mutating `positions.current`/`nextRowRef.current` DURING RENDER — reading and
  // writing a ref outside an effect/event handler is against React's own rules (not just a lint
  // nit: it's exactly the assumption React Compiler / concurrent rendering relies on breaking).
  // `positions`/`nextRowRef` genuinely need to stay refs (an imperative cache keyed by tile id,
  // not something React should re-render for on its own) — the fix is WHERE they're touched, not
  // WHAT they are: the mutation moved into a `useLayoutEffect` (runs synchronously after commit,
  // before paint — same "no visible flash" guarantee the old in-render computation had), and
  // render now only ever reads the plain, ordinary `layout` STATE this effect produces. Reading
  // state during render is always fine; refs are not for exactly this pattern.
  const [layout, setLayout] = useState<{
    laidOut: { tile: CanvasTile; pos: { x: number; y: number } }[];
    nextRunPos: { x: number; y: number };
  }>({ laidOut: [], nextRunPos: { x: 0, y: initialNextRow * 600 } });

  useLayoutEffect(() => {
    const TILE_COL_WIDTH = TILE_SIZE + TILE_GAP;
    // 9:16 portrait tiles can be ~568px tall. 600px safely prevents vertical overlap.
    const TILE_ROW_HEIGHT = 600;

    for (const run of computeRuns(tiles)) {
      const unpositioned = run.tileIds.filter((id) => !positions.current.has(id));
      if (unpositioned.length === 0) continue;

      // A run that's growing (new tiles joined a run some of whose tiles already have real
      // positions from an earlier render) continues packing into that SAME row-band, rather than
      // starting a fresh one below everything — matches the real expectation that a follow-up
      // generation within the same time window joins the group it's actually part of.
      const alreadyPositionedIds = run.tileIds.filter((id) => positions.current.has(id));
      const runStartRow =
        alreadyPositionedIds.length > 0
          ? Math.min(
              ...alreadyPositionedIds.map((id) => Math.round(positions.current.get(id)!.y / TILE_ROW_HEIGHT)),
            )
          : nextRowRef.current;

      // Real, live-found bug (ported fix, 2026-09-25: two freshly generated images overlapped
      // each other) — this used to compute a new tile's slot from its raw INDEX within
      // `run.tileIds`. That was safe when runs were small and time-scoped (each real batch got
      // its own run), but under Product Grouping every element with no `productId` — every
      // element from before this feature existed — now collapses into ONE shared "Unassigned"
      // run. A new tile's index there no longer means "the Nth tile in THIS batch" — it's
      // polluted by unrelated historical tiles whose real stored positions (from the old scheme)
      // don't line up with that index at all, so the computed slot could land exactly on an
      // existing tile. Fixed by tracking which (row, col) slots this run's ALREADY-positioned
      // tiles really occupy, and handing each new tile the next genuinely free slot instead of
      // trusting its array index.
      const occupiedSlots = new Set(
        alreadyPositionedIds.map((id) => {
          const p = positions.current.get(id)!;
          const row = Math.round(p.y / TILE_ROW_HEIGHT) - runStartRow;
          const col = Math.round(p.x / TILE_COL_WIDTH);
          return `${row}:${col}`;
        }),
      );
      let searchRow = 0;
      let searchCol = 0;
      const claimNextFreeSlot = () => {
        while (occupiedSlots.has(`${searchRow}:${searchCol}`)) {
          searchCol = (searchCol + 1) % COLUMNS;
          if (searchCol === 0) searchRow++;
        }
        const slot = { row: searchRow, col: searchCol };
        occupiedSlots.add(`${searchRow}:${searchCol}`);
        searchCol = (searchCol + 1) % COLUMNS;
        if (searchCol === 0) searchRow++;
        return slot;
      };

      run.tileIds.forEach((id) => {
        if (positions.current.has(id)) return;
        const { row, col } = claimNextFreeSlot();
        positions.current.set(id, {
          x: col * TILE_COL_WIDTH,
          y: (runStartRow + row) * TILE_ROW_HEIGHT,
        });
      });

      const maxRowUsed = Math.max(
        runStartRow,
        ...run.tileIds.map((id) => Math.round(positions.current.get(id)!.y / TILE_ROW_HEIGHT)),
      );
      nextRowRef.current = Math.max(nextRowRef.current, maxRowUsed + 1 + RUN_ROW_GAP);
    }

    setLayout({
      laidOut: tiles.map((t) => ({ tile: t, pos: positions.current.get(t.id)! })),
      // Where a genuinely NEW run (e.g. the loading silhouette for a turn in flight) would start —
      // always a fresh row-band below everything already laid out, same as a real new run would get.
      nextRunPos: { x: 0, y: nextRowRef.current * 600 },
    });
  }, [tiles]);

  const movePosition = useCallback((id: string, x: number, y: number) => {
    positions.current.set(id, { x, y });
    savePositions(sessionId, positions.current);
    setLayout((prev) => ({
      ...prev,
      laidOut: prev.laidOut.map((item) => (item.tile.id === id ? { ...item, pos: { x, y } } : item)),
    }));
  }, [sessionId]);

  const movePositions = useCallback((updates: { id: string; x: number; y: number }[]) => {
    updates.forEach(({ id, x, y }) => positions.current.set(id, { x, y }));
    savePositions(sessionId, positions.current);
    const updateById = new Map(updates.map((u) => [u.id, { x: u.x, y: u.y }]));
    setLayout((prev) => ({
      ...prev,
      laidOut: prev.laidOut.map((item) =>
        updateById.has(item.tile.id) ? { ...item, pos: updateById.get(item.tile.id)! } : item,
      ),
    }));
  }, [sessionId]);

  return { laidOut: layout.laidOut, movePosition, movePositions, nextRunPos: layout.nextRunPos };
}

const PENDING_ICON_BY_KIND: Record<string, string> = {
  image: "🖼️",
  video: "🎬",
  audio: "🔊",
  text: "📝",
};

function CanvasEngine({
  tiles,
  onUndo,
  onRedo,
  onSelectTile,
  referencedTileIds = [],
  onContextMenu,
  pendingGeneration,
  activeSpecialist,
  sessionId,
}: CanvasEngineProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const worldRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const drawCanvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);
  const camRef = useRef<Viewport>({ x: 0, y: 0, scale: 1 });
  const ptr = useRef<PointerState | null>(null);
  const drawing = useRef(false);

  const [mode, setMode] = useState<"pan" | "draw">("pan");
  const [color, setColor] = useState("#ffffff");
  const [showGroups, setShowGroups] = useState(true);
  // Real React state (2026-09-26 lint fix) — was a dummy counter plus a direct
  // `camRef.current.scale` read in the zoom-percentage JSX below, which the React Compiler
  // ESLint rules correctly flag: reading a ref's `.current` during render is unsafe (stale under
  // concurrent rendering, not something React tracks for re-renders). `camRef` itself must stay a
  // ref (an imperative, non-rendering cache for pan/zoom math read inside event handlers), but the
  // one value actually DISPLAYED in render — the zoom percentage — is now real state, set from the
  // same place `forceRender` used to fire (`moveCam`'s rAF callback below), so the render always
  // reads a plain value instead of reaching into a ref.
  const [zoomPct, setZoomPct] = useState(100);

  const { laidOut, movePosition, movePositions, nextRunPos } = useTileLayout(tiles, sessionId);
  const timelineGroups = useMemo(() => computeTimelineFrames(tiles, laidOut), [tiles, laidOut]);
  // Product Grouping lineage badge (2026-09-25, ported from `poc/frontend`) — resolves a tile's
  // real parent by id for the "🔗 Based on..." badge label, falling back to a generic label if the
  // parent isn't (or is no longer) in the currently-loaded tile set rather than showing nothing.
  const tileById = useMemo(() => new Map(tiles.map((t) => [t.id, t])), [tiles]);
  // Exactly where a genuinely NEW run would start — so the loading silhouette appears in its own
  // fresh row-band below everything else, never overlapping an existing tile OR an existing frame.
  const pendingPos = nextRunPos;

  const applyTransform = useCallback(() => {
    const c = camRef.current;
    if (worldRef.current) worldRef.current.style.transform = `translate(${c.x}px, ${c.y}px) scale(${c.scale})`;
    if (gridRef.current) {
      // Grid spacing scales with zoom level so dots remain pinned to world space
      const gs = Math.max(12, GRID_SIZE * c.scale);
      // Ensure positive wrapped modulo so background position never jumps or inverts
      const posX = ((c.x % gs) + gs) % gs;
      const posY = ((c.y % gs) + gs) % gs;
      const dotRadius = Math.max(1, Math.min(2.2, 1.2 * c.scale));
      const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");
      const dotColor = isDark ? "rgba(255,255,255,0.3)" : "rgba(15,23,42,0.18)";
      gridRef.current.style.backgroundSize = `${gs}px ${gs}px`;
      gridRef.current.style.backgroundPosition = `${posX}px ${posY}px`;
      gridRef.current.style.backgroundImage = `radial-gradient(circle, ${dotColor} ${dotRadius}px, transparent ${dotRadius + 0.6}px)`;
    }
  }, []);

  useEffect(() => {
    applyTransform();
    const handleThemeChange = () => applyTransform();
    window.addEventListener("themechange", handleThemeChange);
    return () => window.removeEventListener("themechange", handleThemeChange);
  }, [applyTransform]);

  const moveCam = useCallback(
    (next: Viewport) => {
      camRef.current = next;
      applyTransform();
      if (rafRef.current != null) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        setZoomPct(Math.round(camRef.current.scale * 100));
      });
    },
    [applyTransform],
  );

  const fitToContent = useCallback(() => {
    if (!rootRef.current || laidOut.length === 0) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const { pos } of laidOut) {
      minX = Math.min(minX, pos.x);
      minY = Math.min(minY, pos.y);
      maxX = Math.max(maxX, pos.x + TILE_SIZE);
      maxY = Math.max(maxY, pos.y + TILE_SIZE);
    }
    const rect = rootRef.current.getBoundingClientRect();
    moveCam(fitViewport({ x: minX, y: minY, w: maxX - minX, h: maxY - minY }, rect.width, rect.height));
  }, [laidOut, moveCam]);

  // Fit once when tiles first arrive (new session, or the first real generation).
  const fittedFor = useRef<number>(-1);
  useEffect(() => {
    if (laidOut.length > 0 && fittedFor.current !== laidOut.length) {
      fittedFor.current = laidOut.length;
      fitToContent();
    }
  }, [laidOut.length, fitToContent]);

  /** Converts a raw `clientX/clientY` (viewport-relative) into a point relative to the canvas
   * root itself. A real, likely offset bug found live: every screen-to-world conversion used
   * `e.clientX/clientY` directly, silently assuming the canvas root sits at viewport (0,0) with
   * no actual check — true only by accident of today's fullscreen layout, and the first thing to
   * break if that ever changes. Always going through the root's own real bounding rect is correct
   * regardless of where the canvas root actually sits on the page.
   *
   * Moved above the wheel effect below it (2026-09-23, `react-hooks` lint fix) — same function,
   * just declared before its first use instead of after; the hooks linter's data-flow analysis
   * flagged the hoisted-but-later-declared order, even though it already ran correctly at runtime. */
  function toLocal(clientX: number, clientY: number) {
    const rect = rootRef.current?.getBoundingClientRect();
    return { x: clientX - (rect?.left ?? 0), y: clientY - (rect?.top ?? 0) };
  }

  function screenToWorld(clientX: number, clientY: number) {
    const local = toLocal(clientX, clientY);
    return new Camera(camRef.current).screenToWorld(local.x, local.y);
  }

  const zoomByFactor = useCallback(
    (factor: number) => {
      if (!rootRef.current) return;
      const rect = rootRef.current.getBoundingClientRect();
      const cx = rect.width / 2;
      const cy = rect.height / 2;
      moveCam(new Camera(camRef.current).zoomAt(cx, cy, factor));
    },
    [moveCam],
  );

  const resetZoom = useCallback(() => {
    if (!rootRef.current) return;
    const rect = rootRef.current.getBoundingClientRect();
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const cam = camRef.current;
    moveCam(new Camera(cam).zoomAt(cx, cy, 1 / cam.scale));
  }, [moveCam]);

  // Keyboard zoom shortcuts (+, -, Ctrl/Cmd+0)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("button, select, input, textarea, a")) return;
      if (e.key === "=" || e.key === "+") {
        e.preventDefault();
        zoomByFactor(1.2);
      } else if (e.key === "-" || e.key === "_") {
        e.preventDefault();
        zoomByFactor(0.8);
      } else if (e.key === "0" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        resetZoom();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [zoomByFactor, resetZoom]);

  // Wheel: pan by default; ctrl/cmd+wheel (pinch/zoom gesture) zooms smoothly at the cursor
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const cam = camRef.current;
      if (e.ctrlKey || e.metaKey) {
        // Smoothly clamped factor for pinch-to-zoom / ctrl+wheel
        const factor = Math.min(1.4, Math.max(0.7, Math.exp(-e.deltaY * 0.008)));
        const local = toLocal(e.clientX, e.clientY);
        moveCam(new Camera(cam).zoomAt(local.x, local.y, factor));
        return;
      }
      moveCam({ ...cam, x: cam.x - e.deltaX, y: cam.y - e.deltaY });
    };

    // Prevent default browser zoom on macOS Safari/Chrome gestures
    const preventGesture = (e: Event) => e.preventDefault();
    el.addEventListener("gesturestart", preventGesture);
    el.addEventListener("gesturechange", preventGesture);
    el.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      el.removeEventListener("gesturestart", preventGesture);
      el.removeEventListener("gesturechange", preventGesture);
      el.removeEventListener("wheel", onWheel);
    };
  }, [moveCam]);

  useEffect(() => {
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  function drawCtx(): CanvasRenderingContext2D | null {
    return drawCanvasRef.current?.getContext("2d") ?? null;
  }

  function strokeAt(sx: number, sy: number, start: boolean) {
    const ctx = drawCtx();
    if (!ctx) return;
    const world = screenToWorld(sx, sy);
    const x = world.x - DRAW_ORIGIN;
    const y = world.y - DRAW_ORIGIN;
    ctx.lineWidth = 3;
    ctx.lineCap = "round";
    ctx.strokeStyle = color;
    if (start) {
      ctx.beginPath();
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
      ctx.stroke();
    }
  }

  function handlePointerDown(e: React.PointerEvent) {
    if (e.button === 2) return;
    if ((e.target as HTMLElement).closest("button, select, input, a")) return;

    if (mode === "draw") {
      drawing.current = true;
      strokeAt(e.clientX, e.clientY, true);
      rootRef.current?.setPointerCapture(e.pointerId);
      return;
    }
    ptr.current = { x: e.clientX, y: e.clientY, startCam: { ...camRef.current }, moved: false };
    rootRef.current?.setPointerCapture(e.pointerId);
  }

  function handlePointerMove(e: React.PointerEvent) {
    if (mode === "draw") {
      if (drawing.current) strokeAt(e.clientX, e.clientY, false);
      return;
    }
    const p = ptr.current;
    if (!p) return;
    const dx = e.clientX - p.x;
    const dy = e.clientY - p.y;
    if (Math.abs(dx) + Math.abs(dy) > 2) p.moved = true;
    if (p.moved) moveCam({ ...p.startCam, x: p.startCam.x + dx, y: p.startCam.y + dy });
  }

  function endGesture() {
    ptr.current = null;
    drawing.current = false;
  }

  function clearDrawing() {
    const ctx = drawCtx();
    ctx?.clearRect(0, 0, DRAW_CANVAS_SIZE, DRAW_CANVAS_SIZE);
  }

  function handleTileDown(e: React.PointerEvent<HTMLDivElement>, tileId: string, pos: { x: number; y: number }) {
    if (e.button === 2) return;
    if (mode !== "pan") return; // drawing mode keeps drawing on the canvas, tiles don't drag
    if ((e.target as HTMLElement).closest("button, select, input, a, video")) return;
    e.stopPropagation();
    const el = e.currentTarget;
    const startClientX = e.clientX;
    const startClientY = e.clientY;
    const startPos = { ...pos };
    let maxMove = 0;

    const onMove = (ev: PointerEvent) => {
      const scale = camRef.current.scale;
      const dx = (ev.clientX - startClientX) / scale;
      const dy = (ev.clientY - startClientY) / scale;
      maxMove = Math.max(maxMove, Math.abs(ev.clientX - startClientX), Math.abs(ev.clientY - startClientY));
      movePosition(tileId, startPos.x + dx, startPos.y + dy);
    };
    const onUp = () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      // A real click, not a drag — "reference this element in chat," never fired alongside an
      // actual move so dragging a tile never also selects it.
      if (maxMove < CLICK_MOVE_THRESHOLD) onSelectTile?.(tileId);
    };
    el.setPointerCapture(e.pointerId);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
  }

  function handleGroupDown(e: React.PointerEvent<HTMLDivElement>, tileIds: string[]) {
    if (e.button === 2) return;
    if (mode !== "pan") return; 
    e.stopPropagation();
    const el = e.currentTarget;
    const startClientX = e.clientX;
    const startClientY = e.clientY;
    
    // Capture starting positions of all tiles
    const startPositions = new Map<string, { x: number; y: number }>();
    tileIds.forEach(id => {
      const pos = laidOut.find(l => l.tile.id === id)?.pos;
      if (pos) startPositions.set(id, { ...pos });
    });

    const onMove = (ev: PointerEvent) => {
      const scale = camRef.current.scale;
      const dx = (ev.clientX - startClientX) / scale;
      const dy = (ev.clientY - startClientY) / scale;
      
      const updates: { id: string; x: number; y: number }[] = [];
      tileIds.forEach(id => {
        const startPos = startPositions.get(id);
        if (startPos) {
          updates.push({ id, x: startPos.x + dx, y: startPos.y + dy });
        }
      });
      movePositions(updates);
    };
    const onUp = () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
    };
    el.setPointerCapture(e.pointerId);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
  }

  return (
    <div
      ref={rootRef}
      className="relative h-full w-full overflow-hidden bg-surface-950 transition-colors duration-200"
      style={{ cursor: mode === "draw" ? "crosshair" : "default" }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endGesture}
      onPointerCancel={endGesture}
      onLostPointerCapture={endGesture}
      onContextMenu={(e) => {
        // Real native browser context menus on a tile's own <img>/<video> (save-image-as, etc.)
        // stay untouched — this custom menu is deliberately empty-canvas-only, the same
        // interactive-element bail-out `handlePointerDown` already uses for drag-vs-click.
        if ((e.target as HTMLElement).closest("button, select, input, a, video, img")) return;
        e.preventDefault();
        onContextMenu?.({ clientX: e.clientX, clientY: e.clientY });
      }}
    >
      <div
        ref={gridRef}
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage: "radial-gradient(circle, rgba(15,23,42,0.18) 1.2px, transparent 1.8px)",
          backgroundSize: "28px 28px",
          backgroundPosition: "0px 0px",
        }}
      />

      <div ref={worldRef} className="absolute left-0 top-0 origin-top-left">
        {/* Timeline-based grouping (2026-09-22) — rendered behind the tiles (earlier in DOM order,
         * same stacking context, no explicit z-index needed) and `pointer-events-none` so a frame
         * never intercepts a click/drag meant for the tiles inside it. Purely a derived visual
         * overlay: frames aren't their own draggable object, they just recompute their bounding
         * box from wherever their member tiles currently sit — an honest, disclosed simplification
         * versus a real Figma-style Frame (which is itself a first-class, independently
         * draggable/nestable object); building that is real extra scope this doesn't take on. */}
        {showGroups &&
          timelineGroups.map((g) => (
            <div
              key={g.id}
              className="absolute rounded-2xl border-2 border-dashed border-surface-700/60 bg-surface-900/20 backdrop-blur-sm"
              style={{ left: g.x, top: g.y, width: g.w, height: g.h, cursor: mode === "pan" ? "grab" : undefined }}
              onPointerDown={(e) => handleGroupDown(e, g.tileIds)}
            >
              <span className="absolute left-3 top-1.5 whitespace-nowrap text-xs font-medium text-surface-400">
                {g.label}
              </span>
            </div>
          ))}
        {/* Canvas Focus Beacon & Living Specialist Activity */}
        {pendingGeneration && (
          <div
            className="pointer-events-none absolute"
            style={{ left: pendingPos.x, top: pendingPos.y, width: TILE_SIZE, height: TILE_SIZE }}
          >
            {/* 1. Radar Sonar Concentric Expanding Rings */}
            <div className="absolute -inset-4 rounded-3xl border border-brand-500/40 animate-[ping_3s_cubic-bezier(0,0,0.2,1)_infinite] opacity-60 pointer-events-none" />
            <div className="absolute -inset-10 rounded-full border border-brand-400/20 animate-[ping_4.5s_cubic-bezier(0,0,0.2,1)_infinite] opacity-35 pointer-events-none" />

            {/* 2. Floating Active Specialist Badge above the tile */}
            <div className="absolute -top-11 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 rounded-full border border-brand-500/60 bg-surface-900/90 px-3.5 py-1.5 shadow-[0_0_20px_rgba(99,102,241,0.5)] backdrop-blur-xl whitespace-nowrap animate-bounce-subtle">
              <span className="text-sm">
                {activeSpecialist?.agent.avatar || (pendingGeneration.kind ? PENDING_ICON_BY_KIND[pendingGeneration.kind] : "✨")}
              </span>
              <span className="text-xs font-medium text-surface-50 tracking-wide">
                {activeSpecialist ? activeSpecialist.agent.name : `Generating ${pendingGeneration.kind || "Asset"}`}
              </span>
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-brand-400" />
              </span>
            </div>

            {/* 3. Glowing Card Silhouette with Shimmer */}
            <div className="relative flex h-full w-full flex-col items-center justify-center gap-3 overflow-hidden rounded-2xl border-2 border-brand-500/50 bg-surface-900/70 shadow-[0_0_30px_rgba(99,102,241,0.25)] backdrop-blur-xl">
              {/* Shimmer sweep effect */}
              <div className="absolute inset-0 bg-gradient-to-r from-transparent via-brand-500/10 to-transparent -translate-x-full animate-[shimmer_2s_infinite]" />

              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-brand-500/10 border border-brand-500/30 text-3xl shadow-inner">
                <span className="animate-pulse">
                  {(pendingGeneration.kind && PENDING_ICON_BY_KIND[pendingGeneration.kind]) || "✨"}
                </span>
              </div>
              <div className="flex flex-col items-center text-center px-4">
                <span className="text-xs font-semibold uppercase tracking-wider text-brand-300">
                  {activeSpecialist?.taskLabel || `Synthesizing ${pendingGeneration.kind || "Element"}`}
                </span>
                <span className="text-[11px] text-surface-400 font-mono mt-0.5">
                  Autonomous Agent Active
                </span>
              </div>
            </div>
          </div>
        )}
        {laidOut.map(({ tile, pos }) => (
          <div
            key={tile.id}
            className={`absolute overflow-hidden rounded-2xl border border-surface-700/60 bg-surface-900/90 shadow-xl backdrop-blur-xl transition-shadow ${
              referencedTileIds.includes(tile.id) ? "ring-4 ring-brand-500 shadow-[0_0_20px_rgba(99,102,241,0.4)]" : ""
            }`}
            style={{ left: pos.x, top: pos.y, width: TILE_SIZE, cursor: mode === "pan" ? "grab" : undefined }}
            onPointerDown={(e) => handleTileDown(e, tile.id, pos)}
          >
            {tile.kind === "video" ? (
              <video src={tile.url} controls className="w-full" />
            ) : tile.kind === "audio" ? (
              <div className="flex h-24 w-full items-center justify-center bg-surface-900/80 p-3">
                <audio src={tile.url} controls className="w-full" />
              </div>
            ) : tile.kind === "text" ? (
              // A real text CARD, not a media tile — no url/asset at all for these (the text
              // itself IS the content). A fixed max-height + scroll rather than growing
              // unboundedly, since a shot list/scene description can be genuinely long.
              <div className="max-h-80 overflow-y-auto whitespace-pre-wrap p-4 text-sm text-surface-200 bg-surface-800/40 backdrop-blur-sm rounded-md border border-surface-700/50">
                {tile.content}
              </div>
            ) : (
              // eslint-disable-next-line @next/next/no-img-element -- a dynamic, backend-served
              // asset URL, not a static build-time asset next/image is meant for.
              <img
                src={canvasTileImageUrl(tile.url)}
                alt=""
                className="w-full"
                draggable={false}
                loading="lazy"
              />
            )}

            {/* A real caption, not a fabricated one (2026-09-22, per an explicit user ask: "label
             * everything properly and relative to what's generated") — `tile.description`, the
             * backend's own real recorded content for this element. Text tiles already show their
             * full content above, so no caption is added for those (it'd be a redundant repeat of
             * the same text). Two lines max with a fade-truncate via `line-clamp`, full text on
             * hover via `title` — a long session's many tiles need to be tellable apart at a
             * glance, not force-expanded into a wall of text. */}
            {tile.kind !== "text" && tile.description && (
              <p
                className="line-clamp-2 border-t border-neutral-100 bg-white/10 backdrop-blur-md rounded-md px-2 py-1.5 text-[11px] leading-snug text-neutral-200"
                title={tile.description}
              >
                {tile.description}
              </p>
            )}

            {/* Product Grouping lineage badge (2026-09-25, ported from `poc/frontend`) — a real
             * `parent_element_id` set server-side from the turn's referenced element, never a
             * guess. */}
            {tile.parentElementId && (
              <p
                className="border-t border-neutral-100 bg-blue-500/10 px-2 py-1 text-[10px] leading-snug text-blue-200"
                title={
                  tileById.get(tile.parentElementId)?.description
                    ? `Based on: ${tileById.get(tile.parentElementId)!.description}`
                    : "Based on a referenced element"
                }
              >
                🔗 Based on {tileById.get(tile.parentElementId)?.description
                  ? tileById.get(tile.parentElementId)!.description!.slice(0, 40)
                  : "referenced element"}
              </p>
            )}

            {/* Locked to this tile because it's a plain DOM child of it — moves for free when the
             * tile is dragged, no separate position ever tracked for the badge itself. */}
            {tile.hasComment && (
              <div
                className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-amber-500 text-xs text-white shadow"
                title="Has a comment"
              >
                💬
              </div>
            )}

            {tile.complianceStatus === "running" && (
              <div
                className="absolute left-2 top-2 flex items-center gap-1 rounded-full bg-neutral-900/80 px-2 py-1 text-xs text-white shadow"
                title="Compliance QA is running on this element"
              >
                <span className="h-2 w-2 animate-pulse rounded-full bg-blue-400" />
                Running QA…
              </div>
            )}

            {tile.complianceStatus === "failed" && (
              <div
                className="absolute left-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-red-600 text-xs font-bold text-white shadow"
                title="Failed compliance QA — see the canvas Elements panel for details"
              >
                ✕
              </div>
            )}
            
            {tile.alignmentWarning && (
              <div
                className="absolute right-10 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-amber-500 text-xs font-bold text-white shadow cursor-help"
                title={tile.alignmentWarning}
              >
                ⚠️
              </div>
            )}

            {tile.versionCount != null && tile.versionCount > 1 && (
              <div className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-2 bg-surface-900/80 backdrop-blur-md py-1 text-xs text-surface-200 border-t border-surface-700/50">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onUndo?.(tile.id);
                  }}
                  disabled={(tile.version ?? 1) <= 1}
                  className="disabled:opacity-30"
                >
                  ‹
                </button>
                <span>
                  v{tile.version}/{tile.versionCount}
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onRedo?.(tile.id);
                  }}
                  disabled={(tile.version ?? 1) >= (tile.maxVersion ?? tile.versionCount)}
                  className="disabled:opacity-30"
                >
                  ›
                </button>
              </div>
            )}
          </div>
        ))}
        <canvas
          ref={drawCanvasRef}
          width={DRAW_CANVAS_SIZE}
          height={DRAW_CANVAS_SIZE}
          className="absolute"
          style={{
            left: DRAW_ORIGIN,
            top: DRAW_ORIGIN,
            pointerEvents: "none",
          }}
        />
      </div>

      <div className="pointer-events-auto absolute top-1/2 -translate-y-1/2 right-6 z-20 flex flex-col items-center gap-2 rounded-2xl border border-surface-700/50 bg-surface-900/60 p-2 shadow-2xl backdrop-blur-xl animate-fade-in-up w-28">
        {/* Mode selector */}
        <div className="flex flex-col gap-1 w-full">
          <button
            onClick={() => setMode("pan")}
            className={`flex items-center justify-center gap-1.5 rounded-xl px-2 py-1.5 text-xs font-medium transition-all ${
              mode === "pan"
                ? "bg-brand-500 text-white shadow-[0_0_12px_rgba(99,102,241,0.5)]"
                : "text-surface-400 hover:bg-surface-800/60 hover:text-surface-50"
            }`}
            title="Select & Pan"
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 15l-2 5L9 9l11 4-5 2zm0 0l5 5M7.188 2.239l.777 2.897M5.136 7.965l-2.898-.777M13.95 4.05l-2.122 2.122m-5.657 5.656l-2.12 2.122" />
            </svg>
            <span>Pan</span>
          </button>
          <button
            onClick={() => setMode("draw")}
            className={`flex items-center justify-center gap-1.5 rounded-xl px-2 py-1.5 text-xs font-medium transition-all ${
              mode === "draw"
                ? "bg-brand-500 text-white shadow-[0_0_12px_rgba(99,102,241,0.5)]"
                : "text-surface-400 hover:bg-surface-800/60 hover:text-surface-50"
            }`}
            title="Freehand Draw"
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
            </svg>
            <span>Draw</span>
          </button>
        </div>

        {/* Color Palette Swatches */}
        <div className="flex flex-col items-center gap-2 py-2 border-y border-surface-700/50 w-full">
          <div className="grid grid-cols-3 gap-1.5">
            {["#ffffff", "#f43f5e", "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6"].map((swatch) => (
              <button
                key={swatch}
                onClick={() => setColor(swatch)}
                aria-label={`Draw in ${swatch}`}
                className={`h-4 w-4 rounded-full transition-all hover:scale-110 ${
                  color.toLowerCase() === swatch.toLowerCase()
                    ? "ring-2 ring-brand-400 ring-offset-1 ring-offset-surface-900 scale-110"
                    : "border border-white/20 opacity-80 hover:opacity-100"
                }`}
                style={{ backgroundColor: swatch }}
              />
            ))}
          </div>
          <label
            className="group relative flex h-4 w-full cursor-pointer items-center justify-center rounded-lg border border-surface-700 bg-surface-800/60 hover:border-surface-500 transition-colors"
            title="Custom color"
          >
            <input
              type="color"
              value={color}
              onChange={(e) => setColor(e.target.value)}
              className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
            />
            <span
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: color }}
            />
          </label>
        </div>

        {/* Action Controls */}
        <div className="flex flex-col gap-1 w-full">
          <button
            onClick={clearDrawing}
            className="flex items-center justify-center gap-1.5 w-full rounded-xl px-2 py-1.5 text-xs font-medium text-surface-400 hover:bg-red-500/10 hover:text-red-400 transition-colors"
            title="Clear drawing strokes"
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
            <span>Clear</span>
          </button>
          <button
            onClick={fitToContent}
            className="flex items-center justify-center gap-1.5 w-full rounded-xl px-2 py-1.5 text-xs font-medium text-surface-300 hover:bg-surface-800/60 hover:text-surface-50 transition-colors"
            title="Fit view to all content"
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
            </svg>
            <span>Fit</span>
          </button>

          {/* Interactive Zoom Controls */}
          <div className="flex items-center justify-between w-full rounded-xl bg-surface-800/50 p-0.5 border border-surface-700/50 mt-0.5">
            <button
              onClick={() => zoomByFactor(0.8)}
              className="flex h-5 w-5 items-center justify-center rounded-lg text-surface-400 hover:bg-surface-700/60 hover:text-surface-50 transition-colors"
              title="Zoom out (−)"
              aria-label="Zoom out"
            >
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M20 12H4" />
              </svg>
            </button>
            <button
              onClick={resetZoom}
              className="text-[10px] font-mono text-surface-300 hover:text-surface-50 transition-colors px-1"
              title="Reset zoom to 100% (Ctrl+0)"
              aria-label="Reset zoom to 100%"
            >
              {zoomPct}%
            </button>
            <button
              onClick={() => zoomByFactor(1.25)}
              className="flex h-5 w-5 items-center justify-center rounded-lg text-surface-400 hover:bg-surface-700/60 hover:text-surface-50 transition-colors"
              title="Zoom in (+)"
              aria-label="Zoom in"
            >
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 4v16m8-8H4" />
              </svg>
            </button>
          </div>
          {timelineGroups.length > 0 && (
            <button
              onClick={() => setShowGroups((s) => !s)}
              className={`flex items-center justify-center gap-1.5 w-full rounded-xl px-2 py-1.5 text-xs font-medium transition-all ${
                showGroups
                  ? "bg-brand-500 text-white shadow-[0_0_12px_rgba(99,102,241,0.5)]"
                  : "text-surface-400 hover:bg-surface-800/60 hover:text-surface-50"
              }`}
            >
              <span>Group by product</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// React.memo (2026-10-05, FRONTEND_AUDIT.md #13) — same caveat as CanvasView's own memo: only
// effective once CanvasView.tsx (its sole caller) also stabilizes the props it passes down.
export default memo(CanvasEngine);
