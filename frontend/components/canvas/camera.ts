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
  // Real, live-found bug (2026-09-23, reported as "the canvas is going black"): a long-lived
  // session with many real elements spread across a full day's worth of real timestamps computes
  // a genuinely tiny "everything fits" scale — confirmed live on a real 26-element session, the
  // real computed scale hit this floor at 0.15, where a 200px tile renders at 30px, effectively
  // invisible against the canvas's own dark background; almost the entire viewport reads as an
  // empty black void even though every element is real and correctly positioned. Raised to 0.4 —
  // a real trade-off, not a free fix: content that's spread out enough may no longer ALL fit in
  // one screen at once, but what IS visible is now actually visible, and the real pan/zoom
  // controls this canvas already has are the correct way to reach anything that doesn't.
  const s = Math.max(0.4, scale);
  return {
    x: (screenW - box.w * s) / 2 - box.x * s,
    y: (screenH - box.h * s) / 2 - box.y * s,
    scale: s,
  };
}
