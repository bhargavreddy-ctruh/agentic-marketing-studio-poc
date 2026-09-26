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
import urllib.request
from pathlib import Path
from typing import ClassVar

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
    input_schema: ClassVar[dict] = {
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
        raw_ref = args.get("storage_ref") or args.get("reference_storage_ref") or args.get("image_storage_ref") or args.get("video_storage_ref") or ""
        storage_ref = str(raw_ref).strip()
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

        place_fn = _PLACEMENTS.get(placement, _PLACEMENTS["lower third"])

        # Try SVG backend if requested and cairosvg is installed
        if backend == "svg" and _CAIROSVG_AVAILABLE:
            try:
                font_path = _FONT_DIR / f"{font_family.lower().strip().replace(' ', '_')}.ttf"
                font_src = f"file://{font_path.absolute()}" if font_path.exists() else ""
                
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                if not lines:
                    lines = [text]

                headline = lines[0]
                subheads = lines[1:]

                headline_size = max(32, width // 12)
                subhead_size = max(20, width // 26)

                # Approximate total height for placement
                total_text_h = headline_size + (len(subheads) * subhead_size * 1.3)
                # Approximate width using the longest string
                max_chars = max(len(l) for l in lines)
                approx_text_w = max_chars * (headline_size * 0.6)

                place_fn = _PLACEMENTS.get(placement, _PLACEMENTS["lower third"])
                x, y = place_fn(width, height, approx_text_w, total_text_h)
                
                center_x = x + approx_text_w // 2
                
                font_face_rule = f"@font-face {{ font-family: '{font_family}'; src: url('{font_src}'); }}" if font_src else ""

                # Build tspan elements
                tspan_html = f'<tspan x="{center_x}" dy="0" class="overlay-headline">{headline}</tspan>'
                for sub in subheads:
                    tspan_html += f'\n<tspan x="{center_x}" dy="{subhead_size * 1.4}" class="overlay-subhead">{sub}</tspan>'

                svg_content = f"""<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
                  <defs>
                    <style>
                      {font_face_rule}
                      .overlay-headline {{
                        font-family: '{font_family}', sans-serif;
                        font-size: {headline_size}px;
                        font-weight: 800;
                        fill: {text_color_hex};
                        filter: drop-shadow(0px 4px 12px rgba(0,0,0,0.85)) drop-shadow(0px 2px 4px rgba(0,0,0,0.7));
                      }}
                      .overlay-subhead {{
                        font-family: '{font_family}', sans-serif;
                        font-size: {subhead_size}px;
                        font-weight: 400;
                        fill: {text_color_hex};
                        filter: drop-shadow(0px 2px 6px rgba(0,0,0,0.85));
                      }}
                    </style>
                  </defs>
                  <text x="{center_x}" y="{y + headline_size}" text-anchor="middle">{tspan_html}</text>
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
                import traceback
                traceback.print_exc()
                # Fall back cleanly to Pillow rendering below

        # Fallback / Pillow backend
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if not lines:
            lines = [text]
            
        headline = lines[0]
        subheads = lines[1:]

        headline_size = max(32, width // 12)
        subhead_size = max(20, width // 26)
        
        font_headline = _get_font(font_family, headline_size)
        font_subhead = _get_font(font_family, subhead_size)
        
        # Calculate bounding boxes
        h_bbox = draw.textbbox((0,0), headline, font=font_headline)
        h_w, h_h = h_bbox[2] - h_bbox[0], h_bbox[3] - h_bbox[1]
        
        s_h = 0
        s_w = 0
        if subheads:
            s_bbox = draw.multiline_textbbox((0,0), "\n".join(subheads), font=font_subhead)
            s_w, s_h = s_bbox[2] - s_bbox[0], s_bbox[3] - s_bbox[1]
            
        total_w = max(h_w, s_w)
        total_h = h_h + s_h + (subhead_size // 2 if subheads else 0)
        
        place_fn = _PLACEMENTS.get(placement, _PLACEMENTS["lower third"])
        x, y = place_fn(width, height, total_w, total_h)
        
        # Draw Headline
        hx = x + (total_w - h_w) // 2
        stroke_width_h = max(1, headline_size // 15)
        draw.text((hx, y), headline, font=font_headline, fill=text_color, stroke_width=stroke_width_h, stroke_fill=(0, 0, 0, 200))
        
        # Draw Subheads
        if subheads:
            sx = x + (total_w - s_w) // 2
            sy = y + h_h + (subhead_size // 2)
            stroke_width_s = max(1, subhead_size // 15)
            draw.multiline_text((sx, sy), "\n".join(subheads), font=font_subhead, fill=text_color, align="center", stroke_width=stroke_width_s, stroke_fill=(0, 0, 0, 200))

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
