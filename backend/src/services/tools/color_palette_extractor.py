"""
Color Palette Extractor tool — Architecture.md section 1b. Tier 0: deterministic color-theory
code, no AI model at all (per the source PDF's own description). Given a real image, it extracts
actual dominant colors via Pillow's color quantization — no model call, no hallucination risk on
"what color is this."
"""
from __future__ import annotations

import io
from typing import ClassVar

from PIL import Image

from ...core.local_storage import load_asset
from .base import Tool, ToolResult
from .registry import register_tool


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


@register_tool("color_palette_extractor")
class ColorPaletteExtractorTool(Tool):
    name = "color_palette_extractor"
    description = "Extracts the dominant colors from an existing image, deterministically."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"storage_ref": {"type": "string"}, "num_colors": {"type": "integer", "default": 5}},
        "required": ["storage_ref"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        ctx = context or {}
        explicit_ref = str(
            args.get("storage_ref") or args.get("reference_storage_ref") or args.get("image_storage_ref") or ""
        ).strip()
        context_ref = str(ctx.get("reference_storage_ref") or "").strip()

        # Same real, live-found fix as visual_palette_analyzer.py (2026-09-25) — an `or` chain let
        # a bad explicit storage_ref from the model permanently shadow a correct context value.
        # Try the explicit value first, fall back to context only if it fails to resolve.
        storage_ref = explicit_ref or context_ref
        num_colors = int(args.get("num_colors") or 5)
        loaded = await load_asset(storage_ref)
        if loaded is None and context_ref and context_ref != storage_ref:
            storage_ref = context_ref
            loaded = await load_asset(storage_ref)
        if loaded is None:
            return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {storage_ref}")
        image_bytes, _mime = loaded

        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            img = img.resize((150, 150))  # quantizing a huge image is unnecessary work
            quantized = img.quantize(colors=num_colors, method=Image.Quantize.MEDIANCUT)
            palette = quantized.getpalette()[: num_colors * 3]
            colors = [
                _rgb_to_hex(tuple(palette[i : i + 3])) for i in range(0, len(palette), 3)
            ]
        except Exception as exc:  # a genuinely malformed image, not a network/vendor failure
            return ToolResult(ok=False, data={}, error=f"could not read image: {exc}")

        return ToolResult(ok=True, data={"colors": colors})
