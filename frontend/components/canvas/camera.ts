/**
 * Camera — pure viewport math for the infinite canvas. Ported (near-verbatim, it was already
 * correct and small) from the user's own prior project at
 * `~/Downloads/luma_agents/client/src/utils/camera.ts` — their own code, zero licensing concerns,
 * chosen over tldraw's built-in camera specifically so this canvas has no third-party dependency
 * at all (Memory.md: the user's explicit ask to avoid any future licensing question).
 */
export interface Viewport {
  x: number;
  y: number;
  scale: number;
}

export interface WorldBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export class Camera {
  constructor(public vp: Viewport) {}

  screenToWorld(sx: number, sy: number): { x: number; y: number } {
    return { x: (sx - this.vp.x) / this.vp.scale, y: (sy - this.vp.y) / this.vp.scale };
  }

  /** Zoom centered on a screen point. */
  zoomAt(sx: number, sy: number, factor: number, min = 0.12, max = 3): Viewport {
    const scale = Math.min(max, Math.max(min, this.vp.scale * factor));
    const k = scale / this.vp.scale;
    const x = sx - (sx - this.vp.x) * k;
    const y = sy - (sy - this.vp.y) * k;
    return { x, y, scale };
  }
}

/** Fit a viewport so a world-space box is fully visible, centered. */
export function fitViewport(box: WorldBox, screenW: number, screenH: number, padding = 120): Viewport {
  if (!box.w && !box.h) return { x: screenW / 2, y: screenH / 2, scale: 1 };
  const scale = Math.min((screenW - padding * 2) / Math.max(box.w, 1), (screenH - padding * 2) / Math.max(box.h, 1), 1);
  const s = Math.max(0.15, scale);
  return {
    x: (screenW - box.w * s) / 2 - box.x * s,
    y: (screenH - box.h * s) / 2 - box.y * s,
    scale: s,
  };
}
