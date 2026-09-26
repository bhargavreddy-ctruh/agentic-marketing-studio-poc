"""
Visual Palette Analyzer tool — genuine vision for `palette_strategist`, not just hex extraction.

`color_palette_extractor` (Tier 0, Pillow color quantization) answers "what are the exact hex
codes" but has zero scene understanding — it can't tell warm evening light from a flat studio
backdrop, or a moody dark palette from a washed-out one. This tool answers that different
question by actually looking at the image via `complete_with_vision()` (already live, already
used by the compliance checkers — no new provider/account). Kept alongside
`color_palette_extractor`, not replacing it, per an explicit user decision (2026-09-25): exact
hex codes and real mood/lighting understanding are different questions.
"""
from __future__ import annotations

from typing import ClassVar

from ...core.local_storage import load_asset
from ...providers.llm.vision import complete_with_vision
from .base import Tool, ToolResult
from .registry import register_tool

_SYSTEM_PROMPT = (
    "You are a color and mood analyst for marketing visuals. Given a real reference image, "
    "describe its actual color palette (dominant and accent colors, in plain language), overall "
    "mood, and lighting quality. Be specific and grounded in what you actually see — never "
    "invent details not visible in the image. Keep it to 2-4 sentences."
)


@register_tool("visual_palette_analyzer")
class VisualPaletteAnalyzerTool(Tool):
    name = "visual_palette_analyzer"
    description = (
        "Looks at an existing reference image and describes its real color palette, mood, and "
        "lighting — genuine visual understanding, not just extracted hex codes. Use alongside "
        "color_palette_extractor when a reference image is available."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"storage_ref": {"type": "string"}},
        "required": ["storage_ref"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        ctx = context or {}
        explicit_ref = str(args.get("storage_ref") or args.get("reference_storage_ref") or "").strip()
        context_ref = str(ctx.get("reference_storage_ref") or "").strip()

        # Real, live-found gap (2026-09-25, root-caused from a live failure — see this session's
        # own analysis): a plain `or` chain here lets ANY non-empty value the model explicitly
        # passes permanently shadow a known-good context value, even when the model's value is
        # wrong — a genuine risk for a 32-char random hex id, exactly the kind of token a
        # smaller/free-tier model mistypes or hallucinates. Try the model's own value first (it's
        # usually right), but if it doesn't resolve to a real asset and a DIFFERENT real value is
        # sitting in context, retry with that before giving up — never let a bad explicit arg beat
        # a correct fallback that was right there the whole time.
        storage_ref = explicit_ref or context_ref
        if not storage_ref:
            return ToolResult(ok=False, data={}, error="storage_ref is required")

        loaded = load_asset(storage_ref)
        if loaded is None and context_ref and context_ref != storage_ref:
            storage_ref = context_ref
            loaded = load_asset(storage_ref)
        if loaded is None:
            return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {storage_ref}")
        image_bytes, mime_type = loaded
        if not mime_type.startswith("image/"):
            return ToolResult(ok=False, data={}, error=f"storage_ref {storage_ref} is not an image")

        try:
            result = await complete_with_vision(
                image_bytes=image_bytes,
                mime_type=mime_type,
                system=_SYSTEM_PROMPT,
                question="Describe the color palette, mood, and lighting of this image.",
                max_tokens=300,
            )
        except Exception as exc:  # a genuine vendor/provider failure, not a malformed-image case
            return ToolResult(ok=False, data={}, error=f"vision analysis failed: {exc}")

        return ToolResult(ok=True, data={"palette_description": result.text.strip()})
