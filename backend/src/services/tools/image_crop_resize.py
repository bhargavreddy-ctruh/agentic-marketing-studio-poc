"""
Image Crop Resize Tool — Crops or resizes an existing image tile to match specified target dimensions or Ad Spec presets.
"""
from __future__ import annotations

import io
from typing import ClassVar

from PIL import Image, ImageOps

from ...core.local_storage import load_asset, save_asset
from .base import Tool, ToolResult
from .registry import register_tool


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
                "description": "'auto_subject' crops around the main subject using edge detection, 'cover' center-crops, 'contain' pads.",
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
        image_bytes, _ = loaded

        try:
            from PIL import ImageFilter
            with Image.open(io.BytesIO(image_bytes)) as opened:
                img = opened.convert("RGB")

            if crop_mode == "contain":
                padded = ImageOps.pad(img, (target_w, target_h), color=(0, 0, 0))
                out_img = padded
            elif crop_mode == "auto_subject":
                # Find center of mass of the subject using edge detection
                edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
                edges.thumbnail((256, 256), Image.NEAREST)
                ew, eh = edges.size
                total_weight = 0
                cx, cy = 0, 0
                for y in range(eh):
                    for x in range(ew):
                        w_val = edges.getpixel((x, y))
                        total_weight += w_val
                        cx += x * w_val
                        cy += y * w_val
                if total_weight > 0:
                    center_x = (cx / total_weight) / ew
                    center_y = (cy / total_weight) / eh
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
