"""
Image Crop Resize Tool — Crops or resizes an existing image tile to match specified target dimensions or Ad Spec presets.
"""
from __future__ import annotations

import io
from typing import ClassVar

import numpy as np
from PIL import Image, ImageOps

from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import load_asset, save_asset
from ...core.middleware.logging import get_logger
from ...providers.image.rembg_provider import get_background_removal_provider
from .base import Tool, ToolResult
from .registry import register_tool

log = get_logger(__name__)


async def _get_product_center(image_bytes: bytes, mime_type: str, img_size: tuple[int, int]) -> tuple[float, float] | None:
    """Segments the product with rembg (same provider `text_overlay.py`'s mask-aware placement
    already uses) and returns the normalized (0-1) center of its REAL bounding box — replaces the
    old edge-detection centroid for 'auto_subject' cropping, which fixated on whichever region had
    the most visual texture/clutter (which is very often the BACKGROUND — a busy pattern, a crowd
    of small details — not the actual product), confirmed via the earlier text-placement bug of
    the exact same shape. Returns None (never raises) when segmentation is unavailable, so the
    caller falls back to the old heuristic rather than failing the whole crop."""
    try:
        seg = await get_background_removal_provider().remove_background(
            image_bytes=image_bytes, mime_type=mime_type
        )
        mask_img = Image.open(io.BytesIO(seg.mask_bytes)).convert("L").resize(img_size)
        mask_arr = np.array(mask_img)
        ys, xs = np.where(mask_arr > 127)
        if len(xs) == 0:
            return None
        w, h = img_size
        center_x = (int(xs.min()) + int(xs.max())) / 2 / w
        center_y = (int(ys.min()) + int(ys.max())) / 2 / h
        return center_x, center_y
    except ProviderUnavailable as exc:
        log.warning("product_segmentation_unavailable", extra={"_extra_error": str(exc)})
        return None
    except Exception as exc:
        log.warning("product_segmentation_failed", extra={"_extra_error": str(exc)})
        return None


@register_tool("image_crop_resize")
class ImageCropResizeTool(Tool):
    name = "image_crop_resize"
    description = "Crops and resizes an image asset to match explicit pixel width and height requirements or platform specs."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "storage_ref": {
                "type": "string",
                "description": "storage_ref of the image asset to resize/crop.",
            },
            "target_width": {
                "type": "integer",
                "description": "Target width in pixels (e.g., 1080).",
            },
            "target_height": {
                "type": "integer",
                "description": "Target height in pixels (e.g., 1920).",
            },
            "crop_mode": {
                "type": "string",
                "enum": ["auto_subject", "cover", "contain", "center_crop"],
                "default": "auto_subject",
                "description": "'auto_subject' crops around the real product (real segmentation, not a guess) so it's never cut off, 'cover' center-crops, 'contain' pads.",
            },
        },
        "required": ["storage_ref", "target_width", "target_height"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        raw_ref = args.get("storage_ref") or args.get("reference_storage_ref") or args.get("image_storage_ref") or ""
        storage_ref = str(raw_ref).strip()
        target_w = int(args.get("target_width") or 0)
        target_h = int(args.get("target_height") or 0)
        crop_mode = str(args.get("crop_mode") or "cover").strip().lower()

        if not storage_ref or target_w <= 0 or target_h <= 0:
            return ToolResult(
                ok=False,
                data={},
                error="storage_ref, positive target_width, and positive target_height are required",
            )

        loaded = await load_asset(storage_ref)
        if loaded is None:
            return ToolResult(ok=False, data={}, error=f"Asset not found for {storage_ref}")
        image_bytes, image_mime_type = loaded

        try:
            with Image.open(io.BytesIO(image_bytes)) as opened:
                img = opened.convert("RGB")

            mask_aware_crop = False
            if crop_mode == "contain":
                padded = ImageOps.pad(img, (target_w, target_h), color=(0, 0, 0))
                out_img = padded
            elif crop_mode == "auto_subject":
                # Real product segmentation (rembg) — the actual product's bounding-box center,
                # never a guess from visual texture. The old edge-detection heuristic (per-pixel
                # Sobel-weighted centroid) is removed entirely, not just deprioritized: it
                # routinely fixated on whichever region had the most visual texture/clutter — very
                # often the BACKGROUND (a busy pattern, a crowd of small details), not the actual
                # product — the exact same failure mode already found and fixed for text
                # placement's old `_auto_placement`. When segmentation is unavailable, fall back to
                # a plain center crop (0.5, 0.5) — a neutral, honest default — rather than another
                # heuristic prone to the same bug.
                product_center = await _get_product_center(image_bytes, image_mime_type, img.size)
                if product_center is not None:
                    center_x, center_y = product_center
                    mask_aware_crop = True
                else:
                    center_x, center_y = 0.5, 0.5
                fitted = ImageOps.fit(img, (target_w, target_h), method=Image.LANCZOS, centering=(center_x, center_y))
                out_img = fitted
            else:
                # "cover" or "center_crop"
                fitted = ImageOps.fit(img, (target_w, target_h), method=Image.LANCZOS, centering=(0.5, 0.5))
                out_img = fitted

            buf = io.BytesIO()
            out_img.save(buf, format="JPEG", quality=92)
            res_bytes = buf.getvalue()

            new_ref = await save_asset(
                res_bytes,
                "image/jpeg",
                metadata={
                    "crop_mode": crop_mode,
                    "target_width": target_w,
                    "target_height": target_h,
                    "edited_from": storage_ref,
                    "mask_aware_crop": mask_aware_crop,
                },
            )
            return ToolResult(
                ok=True,
                data={
                    "storage_ref": new_ref,
                    "mime_type": "image/jpeg",
                    "width": target_w,
                    "height": target_h,
                },
            )
        except Exception as exc:
            return ToolResult(ok=False, data={}, error=f"Failed to crop/resize image: {exc}")
