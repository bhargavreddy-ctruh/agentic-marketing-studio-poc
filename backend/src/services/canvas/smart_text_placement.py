"""
Mask-aware text placement — the business logic behind `text_overlay`'s "auto" mode.

Given a product's alpha mask, finds the largest empty background regions (classic
"maximal rectangle in a binary matrix", via per-row histogram + stack — the same zero-AI,
zero-cost technique the diagram's "Bounding Box Grid" / distance-transform methods describe) and
auto-wraps/auto-sizes a headline+subhead block to fit the best one. The chosen rectangle is
guaranteed not to touch the product (it's searched on a dilated mask with a safety margin), so the
overlay this produces never lands on top of the product it's selling.

Pure numpy/opencv + string math — no I/O, no vendor SDK, no framework import. Testable with a bare
numpy array, same as any other service per Rules.md section 2.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass

import cv2
import numpy as np

MARGIN_PX = 24  # safety buffer kept between the product and any candidate text rectangle
MIN_RECT_SIZE = 40  # below this, a rectangle isn't worth laying text out in at all


@dataclass
class TextLayout:
    rect: tuple[int, int, int, int]  # (x0, y0, x1, y1) in the mask's own coordinate space
    head_lines: list[str]
    head_size: int
    sub_lines: list[str]
    sub_size: int
    pad: int


def _overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection area as a fraction of the smaller rect's area (used to de-duplicate candidates
    that are really the same region at adjacent row cutoffs)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    iw = max(0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    return inter / min(area_a, area_b)


def candidate_empty_rects(
    mask: np.ndarray,
    margin: int = MARGIN_PX,
    scale_to: int = 300,
    top_k: int = 20,
    pool_size: int = 250,
) -> list[tuple[int, int, int, int]]:
    """
    Finds the `top_k` largest-area axis-aligned rectangles that contain no product pixels.
    `mask` is a single-channel array, foreground (product) pixels > 127. Dilates the product mask
    by `margin` first so no rectangle ever touches its edge. Runs on a downscaled mask for speed;
    coordinates are mapped back to the original resolution.

    Returns several diverse candidates (not just the single biggest-area one) because a
    tall-narrow strip can have more area than a shorter-but-wider one, yet the wider rect lets a
    headline render in a much bigger font — the caller picks by achievable font size, not area. A
    large raw pool is collected first and then thinned by overlap (non-max suppression), since the
    raw top-area list is otherwise dominated by near-identical row-boundary variants of the same
    one or two strips.
    """
    h, w = mask.shape
    scale = min(1.0, scale_to / max(h, w))
    kernel_size = max(1, round(margin * scale))
    fg = (mask > 127).astype(np.uint8) * 255
    if kernel_size > 1:
        fg = cv2.dilate(fg, np.ones((kernel_size, kernel_size), np.uint8))

    small = cv2.resize(
        fg, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_NEAREST
    )
    binary = (small == 0).astype(np.int32)  # 1 = empty/background
    rows, cols = binary.shape

    heights = np.zeros(cols, dtype=np.int32)
    heap: list[tuple[int, tuple[int, int, int, int]]] = []  # min-heap of (area, rect)
    for r in range(rows):
        heights = np.where(binary[r] == 1, heights + 1, 0)
        stack: list[tuple[int, int]] = []
        for c in range(cols + 1):
            hc = int(heights[c]) if c < cols else 0
            start = c
            while stack and stack[-1][1] > hc:
                idx, sh = stack.pop()
                area = sh * (c - idx)
                rect = (idx, r + 1 - sh, c, r + 1)
                if len(heap) < pool_size:
                    heapq.heappush(heap, (area, rect))
                elif area > heap[0][0]:
                    heapq.heapreplace(heap, (area, rect))
                start = idx
            if not stack or stack[-1][1] < hc:
                stack.append((start, hc))

    inv = 1.0 / scale
    pool = [
        (int(x0 * inv), int(y0 * inv), int(x1 * inv), int(y1 * inv))
        for _, (x0, y0, x1, y1) in heap
    ]
    pool.sort(key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), reverse=True)

    diverse: list[tuple[int, int, int, int]] = []
    for rect in pool:
        if all(_overlap_ratio(rect, kept) < 0.5 for kept in diverse):
            diverse.append(rect)
        if len(diverse) >= top_k:
            break
    return diverse


def _estimate_width(text: str, font_size: int) -> float:
    """Cheap per-glyph estimate (bold display faces run wide) used only to decide line wraps —
    final rendering measures the real glyphs and is the source of truth for fit."""
    return len(text) * font_size * 0.62


def _wrap(text: str, font_size: int, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = current + [word]
        if _estimate_width(" ".join(trial), font_size) <= max_width or not current:
            current = trial
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _layout_for_rect(
    headline: str, subhead: str, rect_w: int, rect_h: int, pad: int
) -> tuple[list[str], int, list[str], int]:
    """Picks the largest headline/subhead font sizes (and word-wrap) that fit inside
    (rect_w, rect_h) minus padding, shrinking geometrically until both width and height fit."""
    avail_w = max(10, rect_w - 2 * pad)
    avail_h = max(10, rect_h - 2 * pad)

    head_size = max(14, rect_h // 3)
    head_lines: list[str] = []
    sub_lines: list[str] = []
    for _ in range(60):
        sub_size = max(10, int(head_size * 0.38))
        head_lines = _wrap(headline, head_size, avail_w)
        sub_lines = _wrap(subhead, sub_size, avail_w) if subhead else []

        head_line_h = head_size * 1.15
        sub_line_h = sub_size * 1.3
        gap = head_size * 0.35 if sub_lines else 0
        total_h = len(head_lines) * head_line_h + gap + len(sub_lines) * sub_line_h

        max_w = max(
            [_estimate_width(line, head_size) for line in head_lines]
            + [_estimate_width(line, sub_size) for line in sub_lines]
            + [0]
        )

        if total_h <= avail_h and max_w <= avail_w and head_size >= 14:
            return head_lines, head_size, sub_lines, sub_size
        if head_size <= 14:
            break
        head_size = max(14, int(head_size * 0.88))

    sub_size = max(10, int(head_size * 0.38))
    return (
        _wrap(headline, head_size, avail_w),
        head_size,
        _wrap(subhead, sub_size, avail_w) if subhead else [],
        sub_size,
    )


def rect_overlaps_product(
    mask: np.ndarray, rect: tuple[int, int, int, int], threshold: float = 0.02
) -> bool:
    """True if more than `threshold` of `rect`'s area is product (foreground) pixels. Used to
    verify a placement someone else chose (e.g. an LLM specialist picking a named zone like
    "top-center") before trusting it, rather than only computing placement from scratch."""
    x0, y0, x1, y1 = rect
    h, w = mask.shape
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return False
    region = mask[y0:y1, x0:x1]
    if region.size == 0:
        return False
    return float((region > 127).mean()) > threshold


def choose_best_layout(mask: np.ndarray, headline: str, subhead: str) -> TextLayout | None:
    """Tries each candidate empty rectangle, keeps whichever lets the headline render biggest.
    Returns None if the product leaves no rectangle big enough to be worth using (caller should
    fall back to a different placement strategy)."""
    headline = headline.strip()
    if not headline:
        return None

    best: TextLayout | None = None
    for rect in candidate_empty_rects(mask):
        x0, y0, x1, y1 = rect
        rect_w, rect_h = x1 - x0, y1 - y0
        if rect_w < MIN_RECT_SIZE or rect_h < MIN_RECT_SIZE:
            continue
        pad = max(12, min(rect_w, rect_h) // 12)
        head_lines, head_size, sub_lines, sub_size = _layout_for_rect(
            headline, subhead, rect_w, rect_h, pad
        )
        if best is None or head_size > best.head_size:
            best = TextLayout(
                rect=rect,
                head_lines=head_lines,
                head_size=head_size,
                sub_lines=sub_lines,
                sub_size=sub_size,
                pad=pad,
            )
    return best
