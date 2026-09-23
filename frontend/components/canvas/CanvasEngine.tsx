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
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Camera, Viewport, fitViewport } from "./camera";

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

// Timeline-based grouping (2026-09-22, researched from how photo-library "Moments"
// (Google Photos/Apple Photos) and creative-canvas tools like Luma AI's Boards actually cluster
// items: a real gap-based split on timestamps — start a new group whenever the time since the
// previous item exceeds a threshold — not a fixed calendar bucket (which would arbitrarily split
// a late-night session at midnight) and not a fixed item count (which ignores real pacing). A full
// hour was picked as the threshold: real back-and-forth iteration on one part of a campaign
// (regenerate, comment, a few chat turns) realistically stays under an hour; a gap longer than
// that really does mean the user came back to a different part of the work, not mid-flow.
const GROUP_GAP_MINUTES = 60;
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

/** A contiguous "run" of tiles that belong together — either genuinely GENERATED content close
 * enough in time to be one real creative batch, or a run of UPLOADED content. A real, live-found
 * bug fixed here (2026-09-22): uploads and generated content used to be silently lumped into the
 * same time-based group/frame just because they happened to land close together — a real,
 * misleading claim ("this upload is part of that generation batch") the canvas never actually
 * meant to make. `isUpload` tiles are never framed (see `computeTimelineFrames` below) — this is
 * the ONE function that decides run membership, reused for BOTH position assignment
 * (`useTileLayout`) and frame drawing, so a frame's boundary can never drift out of sync with
 * where its own tiles actually got laid out (the root cause of the earlier overlapping-frames
 * bug: positions were assigned in one continuous grid with no notion of "runs" at all, while
 * frames were drawn around whatever a SEPARATE, inconsistent grouping pass computed). */
interface Run {
  tileIds: string[];
  isUpload: boolean;
}

function computeRuns(tiles: CanvasTile[]): Run[] {
  const runs: Run[] = [];
  let current: CanvasTile[] = [];
  let prevTime: number | null = null;
  let prevIsUpload: boolean | null = null;

  function flush() {
    if (current.length === 0) return;
    runs.push({ tileIds: current.map((t) => t.id), isUpload: current[0].producedBy === "user_upload" });
    current = [];
  }

  for (const tile of tiles) {
    const isUpload = tile.producedBy === "user_upload";
    const t = tile.createdAt ? new Date(tile.createdAt).getTime() : null;
    const gapExceeded = prevTime !== null && t !== null && t - prevTime > GROUP_GAP_MINUTES * 60_000;
    // A run boundary whenever the time gap is real, OR generated/uploaded status changes — never
    // merge an upload into a generated run or vice versa, regardless of how close in time they are.
    if (current.length > 0 && (gapExceeded || isUpload !== prevIsUpload)) flush();
    current.push(tile);
    prevTime = t;
    prevIsUpload = isUpload;
  }
  flush();
  return runs;
}

/** Draws a real frame around EVERY generated run (even a single real item — a real, deliberate
 * change from an earlier pass that skipped framing when there was "only one group": now that
 * uploads are excluded from grouping entirely, a lone generated run sitting next to un-framed
 * uploads is genuinely worth distinguishing, not a redundant whole-canvas border anymore).
 * Uploaded runs are never framed at all — an honest, visible "this wasn't generated" distinction,
 * per an explicit user ask. Reads real element timestamps/specialists for the label; never
 * fabricated. */
function computeTimelineFrames(
  tiles: CanvasTile[],
  laidOut: { tile: CanvasTile; pos: { x: number; y: number } }[],
): TimelineGroup[] {
  const posById = new Map(laidOut.map((l) => [l.tile.id, l.pos]));
  const tileById = new Map(tiles.map((t) => [t.id, t]));
  const frames: TimelineGroup[] = [];

  computeRuns(tiles).forEach((run, i) => {
    if (run.isUpload) return;
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

    const withTime = runTiles.filter((r) => r.tile.createdAt);
    let label: string;
    if (withTime.length > 0) {
      const first = new Date(withTime[0].tile.createdAt!);
      const last = new Date(withTime[withTime.length - 1].tile.createdAt!);
      const sameDay = first.toDateString() === last.toDateString();
      const fmtTime = (d: Date) => d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
      const fmtDate = (d: Date) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      const dateLabel = sameDay
        ? `${fmtDate(first)} · ${fmtTime(first)}–${fmtTime(last)}`
        : `${fmtDate(first)} – ${fmtDate(last)}`;
      const specialists = Array.from(new Set(runTiles.map((r) => r.tile.producedBy).filter(Boolean)));
      const specialistLabel = specialists.length
        ? ` · ${specialists.slice(0, 2).join(", ")}${specialists.length > 2 ? "…" : ""}`
        : "";
      label = `${dateLabel} · ${runTiles.length} item${runTiles.length === 1 ? "" : "s"}${specialistLabel}`;
    } else {
      label = `${runTiles.length} item${runTiles.length === 1 ? "" : "s"}`;
    }

    frames.push({
      id: `run-${i}`,
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

      run.tileIds.forEach((id, i) => {
        if (positions.current.has(id)) return;
        const col = i % COLUMNS;
        const rowWithinRun = Math.floor(i / COLUMNS);
        positions.current.set(id, {
          x: col * TILE_COL_WIDTH,
          y: (runStartRow + rowWithinRun) * TILE_ROW_HEIGHT,
        });
      });

      const rowsUsed = Math.ceil(run.tileIds.length / COLUMNS);
      nextRowRef.current = Math.max(nextRowRef.current, runStartRow + rowsUsed + RUN_ROW_GAP);
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

export default function CanvasEngine({
  tiles,
  onUndo,
  onRedo,
  onSelectTile,
  referencedTileIds = [],
  onContextMenu,
  pendingGeneration,
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
  const [color, setColor] = useState("#1a1a1a");
  const [showGroups, setShowGroups] = useState(true);
  const [, forceRender] = useState(0); // only used to sync React after an imperative camera move

  const { laidOut, movePosition, movePositions, nextRunPos } = useTileLayout(tiles, sessionId);
  const timelineGroups = useMemo(() => computeTimelineFrames(tiles, laidOut), [tiles, laidOut]);
  // Exactly where a genuinely NEW run would start — so the loading silhouette appears in its own
  // fresh row-band below everything else, never overlapping an existing tile OR an existing frame.
  const pendingPos = nextRunPos;

  const applyTransform = useCallback(() => {
    const c = camRef.current;
    if (worldRef.current) worldRef.current.style.transform = `translate(${c.x}px, ${c.y}px) scale(${c.scale})`;
    if (gridRef.current) {
      const gs = GRID_SIZE / c.scale;
      gridRef.current.style.backgroundSize = `${gs}px ${gs}px`;
      gridRef.current.style.backgroundPosition = `${c.x % gs}px ${c.y % gs}px`;
    }
  }, []);

  const moveCam = useCallback(
    (next: Viewport) => {
      camRef.current = next;
      applyTransform();
      if (rafRef.current != null) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        forceRender((n) => n + 1);
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

  // Wheel: pan by default; ctrl/cmd+wheel (pinch/zoom gesture) zooms at the cursor — same feel as
  // the ported reference implementation.
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const cam = camRef.current;
      if (e.ctrlKey || e.metaKey) {
        const factor = Math.exp(-e.deltaY * 0.005);
        const local = toLocal(e.clientX, e.clientY);
        moveCam(new Camera(cam).zoomAt(local.x, local.y, factor));
        return;
      }
      moveCam({ ...cam, x: cam.x - e.deltaX, y: cam.y - e.deltaY });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
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
    // A real bug found live: capturing the pointer here unconditionally swallowed clicks on the
    // toolbar's own buttons (they're DOM children of this same root, so their pointerdown bubbles
    // up to us first) — a plain `button.click()` worked, but the simulated mouse click never did,
    // because pointer capture redirected its matching pointerup away from the button. Bail out
    // before starting a pan/draw gesture if the press actually started on an interactive element.
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
      className="relative h-full w-full overflow-hidden bg-black"
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
        style={{ backgroundImage: "radial-gradient(circle, rgba(255,255,255,0.25) 1px, transparent 1.2px)" }}
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
              className="absolute rounded-2xl border-2 border-dashed border-neutral-300/80 bg-neutral-100/30"
              style={{ left: g.x, top: g.y, width: g.w, height: g.h, cursor: mode === "pan" ? "grab" : undefined }}
              onPointerDown={(e) => handleGroupDown(e, g.tileIds)}
            >
              <span className="absolute left-3 top-1.5 whitespace-nowrap text-xs font-medium text-neutral-500">
                {g.label}
              </span>
            </div>
          ))}
        {/* Real loading silhouette (2026-09-22) — deliberately rendered here, sibling to the
         * group frames and tiles but NEVER wrapped in a timeline group's own boundary (it isn't a
         * real, generated element yet — see this prop's own doc comment). */}
        {pendingGeneration && (
          <div
            className="pointer-events-none absolute flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-neutral-300 bg-neutral-100/80"
            style={{ left: pendingPos.x, top: pendingPos.y, width: TILE_SIZE, height: TILE_SIZE }}
          >
            <span className="animate-pulse text-4xl opacity-60">
              {(pendingGeneration.kind && PENDING_ICON_BY_KIND[pendingGeneration.kind]) || "✨"}
            </span>
            <span className="animate-pulse text-xs font-medium text-neutral-400">
              Generating{pendingGeneration.kind ? ` ${pendingGeneration.kind}` : "…"}
            </span>
          </div>
        )}
        {laidOut.map(({ tile, pos }) => (
          <div
            key={tile.id}
            className={`absolute overflow-hidden rounded-xl border border-white/20 bg-white/10 backdrop-blur-lg shadow-lg ${
              referencedTileIds.includes(tile.id) ? "ring-4 ring-blue-500" : ""
            }`}
            style={{ left: pos.x, top: pos.y, width: TILE_SIZE, cursor: mode === "pan" ? "grab" : undefined }}
            onPointerDown={(e) => handleTileDown(e, tile.id, pos)}
          >
            {tile.kind === "video" ? (
              <video src={tile.url} controls className="w-full" />
            ) : tile.kind === "audio" ? (
              <div className="flex h-24 w-full items-center justify-center bg-neutral-100 p-3">
                <audio src={tile.url} controls className="w-full" />
              </div>
            ) : tile.kind === "text" ? (
              // A real text CARD, not a media tile — no url/asset at all for these (the text
              // itself IS the content). A fixed max-height + scroll rather than growing
              // unboundedly, since a shot list/scene description can be genuinely long.
              <div className="max-h-80 overflow-y-auto whitespace-pre-wrap p-4 text-sm text-neutral-200 bg-black/30 backdrop-blur-sm rounded-md">
                {tile.content}
              </div>
            ) : (
              // eslint-disable-next-line @next/next/no-img-element -- a dynamic, backend-served
              // asset URL, not a static build-time asset next/image is meant for.
              <img src={tile.url} alt="" className="w-full" draggable={false} />
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
              <div className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-2 bg-black/60 py-1 text-xs text-white">
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

      <div className="pointer-events-auto absolute top-1/2 -translate-y-1/2 right-4 z-20 flex flex-col gap-2 rounded-3xl bg-white p-2 shadow-lg">
        <button
          onClick={() => setMode("pan")}
          className={`rounded-full px-3 py-1.5 text-sm ${mode === "pan" ? "bg-neutral-900 text-white" : "text-neutral-600"}`}
        >
          Select / Pan
        </button>
        <button
          onClick={() => setMode("draw")}
          className={`rounded-full px-3 py-1.5 text-sm ${mode === "draw" ? "bg-neutral-900 text-white" : "text-neutral-600"}`}
        >
          Draw
        </button>
        <div className="flex flex-col items-center gap-2 py-2 border-y border-neutral-100">
          {["#1a1a1a", "#e11d48", "#2563eb", "#16a34a", "#f59e0b"].map((swatch) => (
            <button
              key={swatch}
              onClick={() => setColor(swatch)}
              aria-label={`Draw in ${swatch}`}
              className={`h-5 w-5 rounded-full ${color === swatch ? "ring-2 ring-offset-1 ring-neutral-900" : ""}`}
              style={{ backgroundColor: swatch }}
            />
          ))}
          <input
            type="color"
            value={color}
            onChange={(e) => setColor(e.target.value)}
            title="Custom color"
            className="h-5 w-5 cursor-pointer rounded-full border-0 bg-transparent p-0"
          />
        </div>
        <button onClick={clearDrawing} className="rounded-full px-3 py-1.5 text-sm text-neutral-600">
          Clear drawing
        </button>
        <button onClick={fitToContent} className="rounded-full px-3 py-1.5 text-sm text-neutral-600">
          Fit
        </button>
        {timelineGroups.length > 0 && (
          <button
            onClick={() => setShowGroups((s) => !s)}
            className={`rounded-full px-3 py-1.5 text-sm ${showGroups ? "bg-neutral-900 text-white" : "text-neutral-600"}`}
          >
            Group by time
          </button>
        )}
      </div>
    </div>
  );
}
