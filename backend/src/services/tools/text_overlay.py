"""
Text Preserve/Overlay tool — Architecture.md section 1b. Draws real, deterministic text directly
onto an existing image via Pillow — never another diffusion model's guess at legible text.

Real finding from live testing (Memory.md, Phase 3): both `base_image_generator` and `image_editor`
are diffusion models, and diffusion models are unreliable at rendering legible text/logos from a
prompt — asking either to "add a price tag" silently produced no visible text at all. This tool is
the one place in the pipeline that GUARANTEES text appears, pixel-for-pixel as given, because it
draws it directly rather than asking a model to imagine it.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from ...core.local_storage import load_asset, save_asset
from .base import Tool, ToolResult
from .registry import register_tool

_UNSUPPORTED_GLYPHS = {
    "—": "-", "–": "-",  # em dash, en dash
    "‘": "'", "’": "'",  # curly single quotes
    "“": '"', "”": '"',  # curly double quotes
    "…": "...",  # ellipsis
}


def _sanitize_for_default_font(text: str) -> str:
    """PIL's built-in default font (used here for zero-config, guaranteed-available rendering —
    no font file to bundle or fetch) only covers basic Latin glyphs; anything else silently
    renders as a broken-glyph box. Real bug found live (Memory.md, Phase 3): an LLM-generated
    overlay string used an em dash and it rendered as a visible '☒' in the actual output image.
    Swaps the common "smart typography" characters an LLM is likely to produce for their ASCII
    equivalents rather than letting them render as broken boxes."""
    for bad, good in _UNSUPPORTED_GLYPHS.items():
        text = text.replace(bad, good)
    # Anything else outside ASCII (an emoji, an unusual currency symbol, etc.) is dropped rather
    # than rendered as a broken-glyph box — silently keeping a real question mark intact, unlike
    # an encode(errors="replace") pass would.
    return text.encode("ascii", errors="ignore").decode("ascii")


_PLACEMENTS = {
    "lower third": lambda w, h, tw, th: ((w - tw) // 2, int(h * 0.82)),
    "lower third, centered": lambda w, h, tw, th: ((w - tw) // 2, int(h * 0.82)),
    "bottom center": lambda w, h, tw, th: ((w - tw) // 2, int(h * 0.82)),
    "top center": lambda w, h, tw, th: ((w - tw) // 2, int(h * 0.06)),
    "bottom right": lambda w, h, tw, th: (w - tw - 24, h - th - 24),
    "bottom left": lambda w, h, tw, th: (24, h - th - 24),
}


@register_tool("text_overlay")
class TextOverlayTool(Tool):
    name = "text_overlay"
    description = "Draws real, guaranteed-legible text directly onto an existing image at a given placement."
    input_schema = {
        "type": "object",
        "properties": {
            "storage_ref": {"type": "string"},
            "text": {"type": "string"},
            "placement": {"type": "string", "default": "lower third"},
        },
        "required": ["storage_ref", "text"],
    }

    async def run(self, args: dict) -> ToolResult:
        storage_ref = str(args.get("storage_ref") or "")
        text = _sanitize_for_default_font(str(args.get("text") or "").strip())
        placement = str(args.get("placement") or "lower third").strip().lower()
        if not storage_ref or not text:
            return ToolResult(ok=False, data={}, error="storage_ref and text are required")

        loaded = load_asset(storage_ref)
        if loaded is None:
            return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {storage_ref}")
        image_bytes, _mime_type = loaded

        with Image.open(io.BytesIO(image_bytes)) as opened:
            base = opened.convert("RGBA")
        width, height = base.size
        font_size = max(18, width // 18)
        font = ImageFont.load_default(size=font_size)

        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

        place_fn = _PLACEMENTS.get(placement, _PLACEMENTS["lower third"])
        x, y = place_fn(width, height, text_w, text_h)

        pad = font_size // 2
        draw.rectangle(
            [x - pad, y - pad // 2, x + text_w + pad, y + text_h + pad],
            fill=(0, 0, 0, 170),
        )
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

        combined = Image.alpha_composite(base, overlay).convert("RGB")
        buf = io.BytesIO()
        combined.save(buf, format="JPEG", quality=92)
        result_bytes = buf.getvalue()

        new_ref = save_asset(
            result_bytes,
            "image/jpeg",
            metadata={"text_overlay": text, "placement": placement, "edited_from": storage_ref},
        )
        return ToolResult(ok=True, data={"storage_ref": new_ref, "mime_type": "image/jpeg"})
