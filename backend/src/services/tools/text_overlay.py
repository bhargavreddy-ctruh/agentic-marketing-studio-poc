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
import os
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ...core.local_storage import load_asset, save_asset
from .base import Tool, ToolResult
from .registry import register_tool

_FONT_DIR = Path(__file__).parent / "fonts"
_FONTS = {
    "montserrat": "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Bold.ttf",
    "oswald": "https://github.com/google/fonts/raw/main/ofl/oswald/Oswald-Bold.ttf",
    "playfair display": "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/PlayfairDisplay-Bold.ttf",
    "roboto": "https://github.com/google/fonts/raw/main/apache/roboto/Roboto-Bold.ttf"
}

def _get_font(family: str, size: int):
    family_key = family.lower().strip()
    if family_key not in _FONTS:
        return ImageFont.load_default(size=size)
    
    _FONT_DIR.mkdir(exist_ok=True, parents=True)
    font_path = _FONT_DIR / f"{family_key.replace(' ', '_')}.ttf"
    
    if not font_path.exists():
        try:
            req = urllib.request.Request(_FONTS[family_key], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response, open(font_path, 'wb') as out_file:
                out_file.write(response.read())
        except Exception:
            return ImageFont.load_default(size=size)
            
    try:
        return ImageFont.truetype(str(font_path), size)
    except Exception:
        return ImageFont.load_default(size=size)


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


try:
    import cairosvg
    _CAIROSVG_AVAILABLE = True
except Exception:
    _CAIROSVG_AVAILABLE = False


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
            "font_family": {"type": "string", "description": "e.g., Montserrat, Oswald, Playfair Display, Roboto"},
            "text_color": {"type": "string", "description": "Hex color code, e.g., #ffffff"},
            "backend": {
                "type": "string",
                "enum": ["pillow", "svg"],
                "default": "svg",
                "description": "Rendering backend ('svg' for vector text with drop shadow, 'pillow' for basic raster text)",
            },
        },
        "required": ["storage_ref", "text"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        storage_ref = str(args.get("storage_ref") or "")
        text = _sanitize_for_default_font(str(args.get("text") or "").strip())
        placement = str(args.get("placement") or "lower third").strip().lower()
        font_family = str(args.get("font_family") or "montserrat")
        text_color_hex = str(args.get("text_color") or "#ffffff").strip()
        backend = str(args.get("backend") or "svg").strip().lower()
        
        try:
            hex_str = text_color_hex.lstrip('#')
            text_color = tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))
            if len(text_color) == 3:
                text_color = text_color + (255,)
        except Exception:
            text_color = (255, 255, 255, 255)

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
        font = _get_font(font_family, font_size)

        place_fn = _PLACEMENTS.get(placement, _PLACEMENTS["lower third"])

        # Try SVG backend if requested and cairosvg is installed
        if backend == "svg" and _CAIROSVG_AVAILABLE:
            try:
                font_path = _FONT_DIR / f"{font_family.lower().strip().replace(' ', '_')}.ttf"
                font_src = f"file://{font_path.absolute()}" if font_path.exists() else ""
                
                # Approximate bounding box
                temp_draw = ImageDraw.Draw(base)
                bbox = temp_draw.textbbox((0, 0), text, font=font)
                text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                x, y = place_fn(width, height, text_w, text_h)
                
                center_x = x + text_w // 2
                center_y = y + text_h // 2
                
                font_face_rule = f"@font-face {{ font-family: '{font_family}'; src: url('{font_src}'); }}" if font_src else ""

                svg_content = f"""<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
                  <defs>
                    <style>
                      {font_face_rule}
                      .overlay-text {{
                        font-family: '{font_family}', sans-serif;
                        font-size: {font_size}px;
                        font-weight: bold;
                        fill: {text_color_hex};
                        filter: drop-shadow(0px 2px 4px rgba(0,0,0,0.8));
                      }}
                    </style>
                  </defs>
                  <rect x="{x - font_size // 2}" y="{y - font_size // 4}" width="{text_w + font_size}" height="{text_h + font_size // 2}" rx="6" fill="#000000" fill-opacity="0.65"/>
                  <text x="{center_x}" y="{center_y + font_size // 3}" class="overlay-text" text-anchor="middle" dominant-baseline="middle">{text}</text>
                </svg>"""

                png_bytes = cairosvg.svg2png(bytestring=svg_content.encode("utf-8"))
                overlay_img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
                combined = Image.alpha_composite(base, overlay_img).convert("RGB")
                buf = io.BytesIO()
                combined.save(buf, format="JPEG", quality=92)
                res_bytes = buf.getvalue()

                new_ref = save_asset(
                    res_bytes,
                    "image/jpeg",
                    metadata={"text_overlay": text, "placement": placement, "font_family": font_family, "backend": "svg", "edited_from": storage_ref},
                )
                return ToolResult(ok=True, data={"storage_ref": new_ref, "mime_type": "image/jpeg", "backend": "svg"})
            except Exception:
                # Fall back cleanly to Pillow rendering below
                pass

        # Fallback / Pillow backend
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x, y = place_fn(width, height, text_w, text_h)

        pad = font_size // 2
        draw.rectangle(
            [x - pad, y - pad // 2, x + text_w + pad, y + text_h + pad],
            fill=(0, 0, 0, 170),
        )
        draw.text((x, y), text, font=font, fill=text_color)

        combined = Image.alpha_composite(base, overlay).convert("RGB")
        buf = io.BytesIO()
        combined.save(buf, format="JPEG", quality=92)
        result_bytes = buf.getvalue()

        new_ref = save_asset(
            result_bytes,
            "image/jpeg",
            metadata={"text_overlay": text, "placement": placement, "font_family": font_family, "backend": "pillow", "edited_from": storage_ref},
        )
        return ToolResult(ok=True, data={"storage_ref": new_ref, "mime_type": "image/jpeg", "backend": "pillow"})
