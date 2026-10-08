"""
Logo Compositor Tool — Overlays brand logos onto image assets with precise position, scale, and opacity.
"""
from __future__ import annotations

import io
from typing import ClassVar

from PIL import Image, ImageEnhance

from ...core.local_storage import load_asset, save_asset
from .base import Tool, ToolResult
from .registry import register_tool

_POSITIONS = {
    "top_left": lambda w, h, lw, lh, pad: (pad, pad),
    "top_right": lambda w, h, lw, lh, pad: (w - lw - pad, pad),
    "bottom_left": lambda w, h, lw, lh, pad: (pad, h - lh - pad),
    "bottom_right": lambda w, h, lw, lh, pad: (w - lw - pad, h - lh - pad),
    "center": lambda w, h, lw, lh, pad: ((w - lw) // 2, (h - lh) // 2),
}


@register_tool("logo_compositor")
class LogoCompositorTool(Tool):
    name = "logo_compositor"
    description = "Overlays a brand logo onto an image asset with controlled placement, scale, and opacity."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "image_storage_ref": {
                "type": "string",
                "description": "storage_ref of the target image tile.",
            },
            "logo_storage_ref": {
                "type": "string",
                "description": "storage_ref of the brand logo PNG image. Leave blank to automatically use the session's active brand logo.",
            },
            "position": {
                "type": "string",
                "enum": ["top_left", "top_right", "bottom_left", "bottom_right", "center"],
                "default": "bottom_right",
                "description": "Placement quadrant for logo overlay.",
            },
            "logo_scale_pct": {
                "type": "number",
                "default": 15.0,
                "description": "Logo width as percentage of main image width (1.0 - 50.0).",
            },
            "padding_pct": {
                "type": "number",
                "default": 3.0,
                "description": "Padding from edges as percentage of main image width.",
            },
            "opacity": {
                "type": "number",
                "default": 1.0,
                "description": "Logo opacity from 0.0 (transparent) to 1.0 (opaque).",
            },
        },
        "required": ["image_storage_ref"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        image_ref = str(args.get("image_storage_ref") or "").strip()
        
        # Magic fallback to the session's real brand logo, same as base_image_generator
        raw_logo_ref = args.get("logo_storage_ref")
        logo_ref = str(raw_logo_ref or "").strip()
        if not logo_ref and context:
            logo_ref = str(context.get("brand_logo_storage_ref") or "").strip()

        position = str(args.get("position") or "bottom_right").strip().lower()
        logo_scale_pct = float(args.get("logo_scale_pct") or 15.0)
        padding_pct = float(args.get("padding_pct") or 3.0)
        opacity = float(args.get("opacity") or 1.0)

        if not image_ref or not logo_ref:
            return ToolResult(ok=False, data={}, error="image_storage_ref and logo_storage_ref are required")

        img_loaded = await load_asset(image_ref)
        if img_loaded is None:
            return ToolResult(ok=False, data={}, error=f"Target image asset not found for {image_ref}")
        img_bytes, _ = img_loaded

        logo_loaded = await load_asset(logo_ref)
        if logo_loaded is None:
            return ToolResult(ok=False, data={}, error=f"Logo asset not found for {logo_ref}")
        logo_bytes, _ = logo_loaded

        try:
            base_img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
            logo_img = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")

            bw, bh = base_img.size

            target_lw = max(10, int(bw * (logo_scale_pct / 100.0)))
            aspect_ratio = logo_img.height / max(1, logo_img.width)
            target_lh = max(10, int(target_lw * aspect_ratio))

            logo_resized = logo_img.resize((target_lw, target_lh), Image.LANCZOS)

            if opacity < 1.0:
                r, g, b, a = logo_resized.split()
                a = ImageEnhance.Brightness(a).enhance(opacity)
                logo_resized = Image.merge("RGBA", (r, g, b, a))

            padding_px = max(4, int(bw * (padding_pct / 100.0)))
            pos_fn = _POSITIONS.get(position, _POSITIONS["bottom_right"])
            x, y = pos_fn(bw, bh, target_lw, target_lh, padding_px)

            composite = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
            composite.paste(logo_resized, (x, y))

            final_img = Image.alpha_composite(base_img, composite).convert("RGB")
            buf = io.BytesIO()
            final_img.save(buf, format="JPEG", quality=92)
            res_bytes = buf.getvalue()

            new_ref = await save_asset(
                res_bytes,
                "image/jpeg",
                metadata={
                    "logo_composited": True,
                    "position": position,
                    "logo_scale_pct": logo_scale_pct,
                    "edited_from": image_ref,
                },
            )
            return ToolResult(ok=True, data={"storage_ref": new_ref, "mime_type": "image/jpeg"})
        except Exception as exc:
            return ToolResult(ok=False, data={}, error=f"Failed to composite logo: {exc}")
