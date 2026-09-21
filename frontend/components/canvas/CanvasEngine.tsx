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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Camera, Viewport, fitViewport } from "./camera";

export interface CanvasTile {
  id: string;
  kind: "image" | "video";
  url: string;
  /** Real, already-stored data (comment_service.py) — renders a small locked-to-the-tile badge,
   * never a separate tracked position (Memory.md: the badge is a DOM child of the tile itself, so
   * it moves for free whenever the tile is dragged). */
  hasComment?: boolean;
  /** Real version history (versioning_service.py) — when `versionCount` is more than 1, the tile
   * shows a real "vX/Y" strip wired to the actual undo/redo endpoints, not a cosmetic counter. */
  version?: number;
  versionCount?: number;
  /** The real compliance gate's verdict (compliance_gate.py, now run automatically right after
   * generation) — false renders a small red cross badge; true/undefined render nothing (a passed
   * or not-yet-checked element looks the same as before this existed). */
  compliancePassed?: boolean | null;
}

export interface CanvasEngineProps {
  tiles: CanvasTile[];
  onUndo?: (tileId: string) => void;
  onRedo?: (tileId: string) => void;
  /** Fires on a genuine click (not a drag) — "reference this element in chat," distinguished from
   * dragging by the same real-movement threshold the pan gesture already uses for itself. */
  onSelectTile?: (tileId: string) => void;
  referencedTileId?: string | null;
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

interface PointerState {
  x: number;
  y: number;
  startCam: Viewport;
  moved: boolean;
}

/** Assigns each real tile a stable grid position the first time it's seen, so tiles never jump
 * around on a later refresh just because the array was refetched in a different order. Also
 * exposes `movePosition` for real user dragging — a deliberate move a refetch must never undo,
 * which is exactly why positions live in a ref keyed by tile id rather than being recomputed. */
function useTileLayout(tiles: CanvasTile[]) {
  const positions = useRef(new Map<string, { x: number; y: number }>());
  const [, bump] = useState(0);

  const laidOut = useMemo(() => {
    let nextSlot = positions.current.size;
    for (const tile of tiles) {
      if (positions.current.has(tile.id)) continue;
      const col = nextSlot % COLUMNS;
      const row = Math.floor(nextSlot / COLUMNS);
      positions.current.set(tile.id, {
        x: col * (TILE_SIZE + TILE_GAP),
        y: row * (TILE_SIZE + TILE_GAP),
      });
      nextSlot += 1;
    }
    return tiles.map((t) => ({ tile: t, pos: positions.current.get(t.id)! }));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `tiles` is the real trigger; `bump`
    // exists only to force a re-render after an imperative drag, not to recompute this layout.
  }, [tiles]);

  const movePosition = useCallback((id: string, x: number, y: number) => {
    positions.current.set(id, { x, y });
    bump((n) => n + 1);
  }, []);

  return { laidOut, movePosition };
}

export default function CanvasEngine({ tiles, onUndo, onRedo, onSelectTile, referencedTileId }: CanvasEngineProps) {
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
  const [, forceRender] = useState(0); // only used to sync React after an imperative camera move

  const { laidOut, movePosition } = useTileLayout(tiles);

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

  // Wheel: pan by default; ctrl/cmd+wheel (pinch/zoom gesture) zooms at the cursor — same feel as
  // the ported reference implementation.
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const cam = camRef.current;
      if (e.ctrlKey || e.metaKey) {
        const factor = Math.exp(-e.deltaY * 0.0022);
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

  /** Converts a raw `clientX/clientY` (viewport-relative) into a point relative to the canvas
   * root itself. A real, likely offset bug found live: every screen-to-world conversion used
   * `e.clientX/clientY` directly, silently assuming the canvas root sits at viewport (0,0) with
   * no actual check — true only by accident of today's fullscreen layout, and the first thing to
   * break if that ever changes. Always going through the root's own real bounding rect is correct
   * regardless of where the canvas root actually sits on the page. */
  function toLocal(clientX: number, clientY: number) {
    const rect = rootRef.current?.getBoundingClientRect();
    return { x: clientX - (rect?.left ?? 0), y: clientY - (rect?.top ?? 0) };
  }

  function screenToWorld(clientX: number, clientY: number) {
    const local = toLocal(clientX, clientY);
    return new Camera(camRef.current).screenToWorld(local.x, local.y);
  }

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

  /** Real per-tile dragging — a tile's own pointerdown, never the shared root gesture, so moving
   * one tile never also pans the canvas. Uses native listeners on the tile itself (not React's
   * synthetic bubbling) so the drag keeps tracking even if the pointer leaves the tile's own
   * bounds mid-drag, the same reason pointer capture exists at all. */
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

  return (
    <div
      ref={rootRef}
      className="relative h-full w-full overflow-hidden bg-neutral-50"
      style={{ cursor: mode === "draw" ? "crosshair" : "default" }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endGesture}
      onPointerCancel={endGesture}
      onLostPointerCapture={endGesture}
    >
      <div
        ref={gridRef}
        className="pointer-events-none absolute inset-0"
        style={{ backgroundImage: "radial-gradient(circle, rgba(16,21,31,0.15) 1px, transparent 1.2px)" }}
      />

      <div ref={worldRef} className="absolute left-0 top-0 origin-top-left">
        {laidOut.map(({ tile, pos }) => (
          <div
            key={tile.id}
            className={`absolute overflow-hidden rounded-lg bg-white shadow-md ${
              tile.id === referencedTileId ? "ring-4 ring-blue-500" : ""
            }`}
            style={{ left: pos.x, top: pos.y, width: TILE_SIZE, cursor: mode === "pan" ? "grab" : undefined }}
            onPointerDown={(e) => handleTileDown(e, tile.id, pos)}
          >
            {tile.kind === "video" ? (
              <video src={tile.url} controls className="w-full" />
            ) : (
              // eslint-disable-next-line @next/next/no-img-element -- a dynamic, backend-served
              // asset URL, not a static build-time asset next/image is meant for.
              <img src={tile.url} alt="" className="w-full" draggable={false} />
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

            {tile.compliancePassed === false && (
              <div
                className="absolute left-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-red-600 text-xs font-bold text-white shadow"
                title="Failed compliance QA — see the canvas Elements panel for details"
              >
                ✕
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
                  disabled={(tile.version ?? 1) >= tile.versionCount}
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

      <div className="pointer-events-auto absolute bottom-4 left-4 z-20 flex gap-2 rounded-full bg-white p-1 shadow-lg">
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
        <div className="flex items-center gap-1 px-1">
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
      </div>
    </div>
  );
}
