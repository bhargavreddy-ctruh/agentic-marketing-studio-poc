import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CanvasTile } from "../components/canvas/CanvasEngine";

export const GRID_SIZE = 28;
export const TILE_SIZE = 320;
export const TILE_GAP = 48;
export const COLUMNS = 4;
export const FRAME_PADDING = 32;
export const FRAME_LABEL_HEIGHT = 28;
export const RUN_ROW_GAP = 1;

export interface TimelineGroup {
  id: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  tileIds: string[];
}

export interface Run {
  tileIds: string[];
  productId: string | null;
  productName: string | null;
}

export function computeRuns(tiles: CanvasTile[]): Run[] {
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

export function computeTimelineFrames(
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
  }
}

export function useTileLayout(tiles: CanvasTile[], sessionId?: string | null) {
  const seededPositions = useMemo(() => loadSavedPositions(sessionId), [sessionId]);
  const positions = useRef(seededPositions);

  const initialNextRow = useMemo(() => {
    let maxRow = 0;
    seededPositions.forEach(pos => {
      const row = Math.ceil(pos.y / 600);
      if (row > maxRow) maxRow = row;
    });
    return maxRow > 0 ? maxRow + 1 : 0;
  }, [seededPositions]);

  const nextRowRef = useRef(initialNextRow);

  const [layout, setLayout] = useState<{
    laidOut: { tile: CanvasTile; pos: { x: number; y: number } }[];
    nextRunPos: { x: number; y: number };
  }>({ laidOut: [], nextRunPos: { x: 0, y: initialNextRow * 600 } });

  useLayoutEffect(() => {
    const TILE_COL_WIDTH = TILE_SIZE + TILE_GAP;
    const TILE_ROW_HEIGHT = 600;

    for (const run of computeRuns(tiles)) {
      const unpositioned = run.tileIds.filter((id) => !positions.current.has(id));
      if (unpositioned.length === 0) continue;

      const alreadyPositionedIds = run.tileIds.filter((id) => positions.current.has(id));
      const runStartRow =
        alreadyPositionedIds.length > 0
          ? Math.min(
              ...alreadyPositionedIds.map((id) => Math.round(positions.current.get(id)!.y / TILE_ROW_HEIGHT)),
            )
          : nextRowRef.current;

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
