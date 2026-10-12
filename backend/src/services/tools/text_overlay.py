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

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import load_asset, save_asset
from ...core.middleware.logging import get_logger
from ...providers.image.rembg_provider import get_background_removal_provider
from ..canvas.smart_text_placement import TextLayout, choose_best_layout, rect_overlaps_product
from .base import Tool, ToolResult
from .registry import register_tool

log = get_logger(__name__)

_FONT_DIR = Path(__file__).parent / "fonts"
# Real, live-found bug (2026-10-06): these used to point at google/fonts' raw repo
# (".../ofl/montserrat/Montserrat-Bold.ttf") — ALL FOUR now 404, confirmed live via the GitHub API:
# google/fonts migrated every one of these families to variable fonts (a single
# "Montserrat[wght].ttf" covering every weight, no more static per-weight files, no "static/"
# fallback directory either). Switched to the `typeface-<family>` npm packages served via jsDelivr
# (a stable, widely-used CDN) instead — these still ship real, static, per-weight ".woff" files
# (confirmed: no ".ttf" build exists for them either, only .woff/.woff2) at a predictable,
# versioned URL. Confirmed live that Pillow's bundled FreeType loads a .woff directly via
# `ImageFont.truetype()` with no extra handling — same call, just a different real file format.
_FONTS = {
    "montserrat": "https://cdn.jsdelivr.net/npm/typeface-montserrat@1.1.13/files/montserrat-latin-700.woff",
    "oswald": "https://cdn.jsdelivr.net/npm/typeface-oswald@1.1.13/files/oswald-latin-700.woff",
    "playfair display": "https://cdn.jsdelivr.net/npm/typeface-playfair-display@1.1.13/files/playfair-display-latin-700.woff",
    "roboto": "https://cdn.jsdelivr.net/npm/typeface-roboto@1.1.13/files/roboto-latin-700.woff",
}

def _ensure_font_file(family: str) -> Path | None:
    """Ensures `family`'s real, static Bold .woff is downloaded to `_FONT_DIR`, returns its path —
    or None if the family isn't in `_FONTS` or the download genuinely fails. Real, live-found bug
    (2026-10-06): the SVG backend used to only CHECK whether this file already existed, never
    actually fetch it — on a fresh environment (no font files committed, nothing else populates
    `_FONT_DIR` first) that check was always False, so the SVG path's `@font-face` rule was
    silently skipped and every overlay rendered in whatever generic sans-serif font the environment
    happened to have — "boring" overlays, pixel-identical gradient bar, no bold custom display
    face. Split out here so BOTH backends share the one real download path: the SVG backend only
    needs the file's path (for its own `src: url(...)`), the Pillow backend needs this PLUS a
    loaded ImageFont object (built by `_get_font` below)."""
    family_key = family.lower().strip()
    if family_key not in _FONTS:
        return None

    _FONT_DIR.mkdir(exist_ok=True, parents=True)
    font_path = _FONT_DIR / f"{family_key.replace(' ', '_')}.woff"

    if not font_path.exists():
        try:
            req = urllib.request.Request(_FONTS[family_key], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response, open(font_path, 'wb') as out_file:
                out_file.write(response.read())
        except Exception:
            return None

    return font_path


def _get_font(family: str, size: int):
    font_path = _ensure_font_file(family)
    if font_path is None:
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
    "₹": "Rs. ",
    "€": "EUR ",
    "£": "GBP ",
}


def _sanitize_for_default_font(text: str) -> str:
    """PIL's built-in default font (used here for zero-config, guaranteed-available rendering —
    no font file to bundle or fetch) only covers basic Latin glyphs; anything else silently
    renders as a broken-glyph box. Real bug found live (Memory.md, Phase 3): an LLM-generated
    overlay string used an em dash and it rendered as a visible '☒' in the actual output image.
    Swaps the common "smart typography" characters an LLM is likely to produce for their ASCII
    equivalents rather than letting them render as broken boxes."""
    text = text.replace('\\n', '\n')
    for bad, good in _UNSUPPORTED_GLYPHS.items():
        text = text.replace(bad, good)
    # Anything else outside ASCII (an emoji, an unusual currency symbol, etc.) is dropped rather
    # than rendered as a broken-glyph box — silently keeping a real question mark intact, unlike
    # an encode(errors="replace") pass would.
    return text.encode("ascii", errors="ignore").decode("ascii")


def _auto_placement(image: Image.Image) -> str:
    """Uses edge detection to find the flattest/emptiest zone in the image for perfect text placement."""
    img = image.convert("L")
    w, h = img.size
    edges = img.filter(ImageFilter.FIND_EDGES)
    
    zones = {
        "top left": (0, 0, w//3, h//3),
        "top center": (w//3, 0, 2*w//3, h//3),
        "top right": (2*w//3, 0, w, h//3),
        "bottom left": (0, 2*h//3, w//3, h),
        "bottom center": (w//3, 2*h//3, 2*w//3, h),
        "bottom right": (2*w//3, 2*h//3, w, h),
    }
    
    best_zone = "bottom center"
    min_energy = float('inf')
    
    for name, box in zones.items():
        region = edges.crop(box)
        hist = region.histogram()
        energy = sum(i * count for i, count in enumerate(hist))
        if energy < min_energy:
            min_energy = energy
            best_zone = name
            
    return best_zone


async def _get_product_mask(image_bytes: bytes, mime_type: str, base: Image.Image) -> np.ndarray | None:
    """Segments the product with rembg (U2-NetP) and returns its alpha mask as a numpy array
    sized to match `base`, or None if segmentation is unavailable. Used two ways: to compute a
    brand-new mask-aware rectangle for "auto" placement, and to verify a placement someone else
    chose (an LLM specialist naming an explicit zone) doesn't actually land on the product before
    trusting it. Must never be the reason the tool call fails outright — every caller treats None
    as "fall back to the non-mask-aware path", not as an error."""
    try:
        seg = await get_background_removal_provider().remove_background(
            image_bytes=image_bytes, mime_type=mime_type
        )
        mask_img = Image.open(io.BytesIO(seg.mask_bytes)).convert("L").resize(base.size)
        return np.array(mask_img)
    except ProviderUnavailable as exc:
        log.warning("product_segmentation_unavailable", extra={"_extra_error": str(exc)})
        return None
    except Exception as exc:
        log.warning("product_segmentation_failed", extra={"_extra_error": str(exc)})
        return None


def _get_vibrant_accent(image: Image.Image) -> tuple[int, int, int, int]:
    """Extracts the most vibrant color from the image to use as an accent text color."""
    small = image.copy()
    small.thumbnail((150, 150))
    hsv = small.convert("HSV")
    rgb_data = small.load()
    hsv_data = hsv.load()
    
    best_color = (255, 255, 255, 255)
    max_vibrancy = -1
    
    w, h = small.size
    for y in range(h):
        for x in range(w):
            rgba = rgb_data[x, y]
            _, s, v = hsv_data[x, y]
            vibrancy = s * v
            if v < 120: continue # Skip very dark colors
            if vibrancy > max_vibrancy:
                max_vibrancy = vibrancy
                best_color = (rgba[0], rgba[1], rgba[2], 255)
                
    if max_vibrancy <= 0:
        return (255, 255, 255, 255)
    return best_color


_PLACEMENTS = {
    "lower third": lambda w, h, tw, th: ((w - tw) // 2, int(h * 0.85) - th),
    "lower third, centered": lambda w, h, tw, th: ((w - tw) // 2, int(h * 0.85) - th),
    "bottom center": lambda w, h, tw, th: ((w - tw) // 2, int(h * 0.92) - th),
    "top center": lambda w, h, tw, th: ((w - tw) // 2, int(h * 0.08)),
    "bottom right": lambda w, h, tw, th: (w - tw - int(w * 0.05), int(h * 0.95) - th),
    "bottom left": lambda w, h, tw, th: (int(w * 0.05), int(h * 0.95) - th),
    "top left": lambda w, h, tw, th: (int(w * 0.05), int(h * 0.08)),
    "top right": lambda w, h, tw, th: (w - tw - int(w * 0.05), int(h * 0.08)),
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
            "placement": {
                "type": "string", 
                "enum": ["auto", "lower third", "lower third, centered", "bottom center", "top center", "bottom right", "bottom left", "top left", "top right"],
                "default": "auto",
                "description": "Where to place the text. Use 'auto' to intelligently find free space."
            },
            "font_family": {
                "type": "string", 
                "enum": ["Montserrat", "Oswald", "Playfair Display", "Roboto"],
                "description": "e.g., Montserrat, Oswald, Playfair Display, Roboto"
            },
            "text_color": {
                "type": "string",
                "default": "auto",
                "description": "Hex color code for the HEADLINE (the first, large/bold line), e.g. #ffffff. Use 'auto' to extract a vibrant accent color from the image."
            },
            "subhead_color": {
                "type": "string",
                "default": "auto",
                "description": "Hex color code for the SUBHEAD (the smaller line(s) below the headline), e.g. #ffffff. Pick a genuinely different, high-contrast value from text_color — matching the two reads as flat/unclear with no hierarchy. 'auto' picks a safe, high-contrast neutral (near-white/near-black) based on the background, rather than reusing text_color.",
            },
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
        placement = str(args.get("placement") or "lower third").strip().lower().replace("-", " ")
        font_family = str(args.get("font_family") or "montserrat")
        text_color_hex = str(args.get("text_color") or "auto").strip().lower()
        subhead_color_hex = str(args.get("subhead_color") or "auto").strip().lower()
        backend = str(args.get("backend") or "svg").strip().lower()
        
        if not storage_ref or not text:
            return ToolResult(ok=False, data={}, error="storage_ref and text are required")

        loaded = await load_asset(storage_ref)
        if loaded is None:
            return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {storage_ref}")
        image_bytes, _mime_type = loaded

        with Image.open(io.BytesIO(image_bytes)) as opened:
            base = opened.convert("RGBA")
        width, height = base.size

        if text_color_hex == "auto":
            text_color = _get_vibrant_accent(base)
            print(f"Auto-selected text color: {text_color}")
        else:
            try:
                hex_str = text_color_hex.lstrip('#')
                text_color = tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))
                if len(text_color) == 3:
                    text_color = text_color + (255,)
            except Exception:
                text_color = (255, 255, 255, 255)

        # Real, live-found bug (2026-10-12, Flipkart Apple Watch post complaint: "the color
        # schemas are also not clear"): the headline and subhead used to always share this exact
        # same `text_color` — a 10% opacity difference was the only distinction, which reads as
        # flat/unclear against a busy photo background. "auto" now picks a genuinely different,
        # high-contrast neutral (near-white) rather than reusing the headline's own color — the
        # dark contrast gradient already drawn behind the text (see `grad_base` below) makes a
        # near-white subhead legible against almost any photo, regardless of the headline's hue.
        if subhead_color_hex == "auto":
            subhead_color = (245, 245, 245, 255)
        else:
            try:
                hex_str = subhead_color_hex.lstrip('#')
                subhead_color = tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))
                if len(subhead_color) == 3:
                    subhead_color = subhead_color + (255,)
            except Exception:
                subhead_color = (245, 245, 245, 255)

        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if not lines:
            lines = [text]
        subhead_text = " ".join(lines[1:]) if len(lines) > 1 else ""

        requested_placement = placement
        # Always segment, regardless of whether placement is "auto" or an explicit zone: an LLM
        # specialist (e.g. Overlay Artist) is prompted to pick a named zone itself and never sends
        # "auto" at all, so gating this behind placement=="auto" would mean the mask-aware fix
        # never actually ran for real callers — found live (2026-10-12) when an explicit
        # "top center"-ish choice still landed text squarely on the product. Real, disclosed cost:
        # one extra rembg inference (~200-300ms CPU) per call, even for a placement that turns out
        # fine as requested.
        mask_arr = await _get_product_mask(image_bytes, _mime_type, base)
        smart_layout: TextLayout | None = (
            choose_best_layout(mask_arr, lines[0], subhead_text) if mask_arr is not None else None
        )

        if placement == "auto":
            placement = _auto_placement(base)
            print(f"Auto-selected placement (heuristic baseline): {placement}")

        # Use Pillow to accurately measure and wrap text regardless of backend
        dummy_img = Image.new("RGBA", (1, 1))
        dummy_draw = ImageDraw.Draw(dummy_img)

        # Always compute the zone-based (non-mask-aware) layout first — this is the baseline we
        # fall back to when segmentation is unavailable, and (for an explicit placement) what we
        # verify for product overlap below before trusting it.
        # Constrain max width based on the placement zone to prevent spilling
        if "left" in placement or "right" in placement:
            max_w = int(width * 0.45)
        elif "center" in placement and placement != "lower third, centered":
            max_w = int(width * 0.55)
        else:
            max_w = int(width * 0.85)

        def fit_text(text_lines, target_size):
            size = target_size
            while size >= 14:
                font = _get_font(font_family, size)
                wrapped_lines = []
                for line in text_lines:
                    words = line.split()
                    curr = []
                    for w in words:
                        test_line = " ".join(curr + [w])
                        if dummy_draw.textlength(test_line, font=font) <= max_w:
                            curr.append(w)
                        else:
                            if curr:
                                wrapped_lines.append(" ".join(curr))
                                curr = [w]
                            else:
                                wrapped_lines.append(w)
                                curr = []
                    if curr:
                        wrapped_lines.append(" ".join(curr))
                if not any(dummy_draw.textlength(line, font=font) > max_w for line in wrapped_lines):
                    return wrapped_lines, font, size
                size -= 4
            return text_lines, _get_font(font_family, 14), 14

        headline_size = max(24, width // 15)
        headline_lines, font_headline, final_h_size = fit_text([lines[0]], headline_size)

        subhead_size = max(16, int(final_h_size * 0.6))
        subhead_lines, font_subhead, final_s_size = fit_text(lines[1:], subhead_size) if len(lines) > 1 else ([], None, 0)

        h_bbox = dummy_draw.multiline_textbbox((0,0), "\n".join(headline_lines), font=font_headline)
        h_w, h_h = h_bbox[2] - h_bbox[0], h_bbox[3] - h_bbox[1]

        s_w, s_h = 0, 0
        if subhead_lines:
            s_bbox = dummy_draw.multiline_textbbox((0,0), "\n".join(subhead_lines), font=font_subhead)
            s_w, s_h = s_bbox[2] - s_bbox[0], s_bbox[3] - s_bbox[1]

        total_w = max(h_w, s_w)
        # Real, live-found bug (2026-10-12, Flipkart Apple Watch post complaint: "almost
        # unreadable and cluttered"): this gap used to be `subhead_size // 2` — a function of only
        # the SMALLER subhead font's own size, with no contribution from the headline's own line-
        # height/descent clearance. A large bold headline crowded straight into the subhead below
        # it. Matches the ratio `smart_text_placement.py`'s own `_layout_for_rect` already uses for
        # its SIZING/fit decision (`head_size * 0.35`) — anchored on the headline's size, the right
        # basis — plus the subhead's own ascent headroom, so the two don't disagree anymore.
        subhead_gap = (int(final_h_size * 0.35) + int(final_s_size * 0.3)) if subhead_lines else 0
        total_h = h_h + s_h + subhead_gap

        place_fn = _PLACEMENTS.get(placement, _PLACEMENTS["lower third"])
        x, y = place_fn(width, height, total_w, total_h)

        if "left" in placement:
            align = "left"
            hx = int(width * 0.075)
            sx = int(width * 0.075)
        elif "right" in placement:
            align = "right"
            hx = int(width * 0.925) - h_w
            sx = int(width * 0.925) - s_w
        else:
            align = "center"
            hx = x + (total_w - h_w) // 2
            sx = x + (total_w - s_w) // 2

        # Decide whether to trust the zone-based layout above or override it with the mask-aware
        # one: for "auto" there was never a real creative choice to preserve, so prefer the
        # mask-aware rect outright whenever segmentation succeeded. For an explicit zone (the
        # common real case — an LLM specialist naming "top-center" etc.), respect it UNLESS its
        # actual bounding box overlaps the product, in which case the guarantee wins over the
        # creative suggestion.
        use_smart = False
        if smart_layout is not None:
            if requested_placement == "auto":
                use_smart = True
            else:
                sy_bound = y + h_h + subhead_gap + s_h if subhead_lines else y + h_h
                heuristic_rect = (
                    min(hx, sx) if subhead_lines else hx,
                    y,
                    max(hx + h_w, sx + s_w) if subhead_lines else hx + h_w,
                    sy_bound,
                )
                if rect_overlaps_product(mask_arr, heuristic_rect):
                    use_smart = True
                    print(
                        f"Explicit placement '{requested_placement}' overlapped the product — "
                        f"using mask-aware rect instead"
                    )

        if use_smart:
            headline_lines = smart_layout.head_lines
            final_h_size = smart_layout.head_size
            font_headline = _get_font(font_family, final_h_size)
            subhead_lines = smart_layout.sub_lines
            final_s_size = smart_layout.sub_size
            subhead_size = final_s_size
            font_subhead = _get_font(font_family, final_s_size) if subhead_lines else None

            h_bbox = dummy_draw.multiline_textbbox((0,0), "\n".join(headline_lines), font=font_headline)
            h_w, h_h = h_bbox[2] - h_bbox[0], h_bbox[3] - h_bbox[1]
            s_w, s_h = 0, 0
            if subhead_lines:
                s_bbox = dummy_draw.multiline_textbbox((0,0), "\n".join(subhead_lines), font=font_subhead)
                s_w, s_h = s_bbox[2] - s_bbox[0], s_bbox[3] - s_bbox[1]
            # Recompute for the mask-aware layout's own (possibly different) font sizes — see the
            # same gap-formula fix/comment above this closure.
            subhead_gap = (int(final_h_size * 0.35) + int(final_s_size * 0.3)) if subhead_lines else 0

            rx0, ry0, _rx1, _ry1 = smart_layout.rect
            align = "left"
            hx = rx0 + smart_layout.pad
            sx = hx
            y = ry0 + smart_layout.pad
            print(f"Mask-aware placement: rect={smart_layout.rect}, head_size={smart_layout.head_size}")

        # Real, live-found bug (2026-10-12, found while fixing the subhead-color gap above): the
        # SVG backend's CSS `fill` used the RAW `text_color_hex` string, including the literal
        # word "auto" when that was the input — invalid CSS, silently falling back to black rather
        # than the real extracted accent color `_get_vibrant_accent` already computed. Both colors
        # now always resolve to a genuine `#rrggbb` string from the already-resolved RGBA tuples,
        # so "auto" actually works in the (default) SVG path, not just the Pillow fallback.
        text_color_css = f"#{text_color[0]:02x}{text_color[1]:02x}{text_color[2]:02x}"
        subhead_color_css = f"#{subhead_color[0]:02x}{subhead_color[1]:02x}{subhead_color[2]:02x}"

        # Try SVG backend if requested and cairosvg is installed
        if backend == "svg" and _CAIROSVG_AVAILABLE:
            try:
                font_path = _ensure_font_file(font_family)
                font_src = f"file://{font_path.absolute()}" if font_path else ""
                
                # SVG uses middle anchors for center alignment, start for left, end for right
                if align == "left":
                    text_anchor = "start"
                    anchor_hx = hx
                    anchor_sx = sx
                elif align == "right":
                    text_anchor = "end"
                    anchor_hx = hx + h_w
                    anchor_sx = sx + s_w
                else:
                    text_anchor = "middle"
                    anchor_hx = hx + (h_w // 2)
                    anchor_sx = sx + (s_w // 2)
                
                font_face_rule = f"@font-face {{ font-family: '{font_family}'; src: url('{font_src}'); }}" if font_src else ""

                # Dynamically choose gradient color based on text luminance for contrast
                luminance = 0.299 * text_color[0] + 0.587 * text_color[1] + 0.114 * text_color[2]
                grad_base = "255,255,255" if luminance < 128 else "0,0,0"

                if use_smart:
                    # Text always starts at the TOP of its own rect and grows downward here, and
                    # the rect can be any shape (a side strip, not just a horizontal band) — so the
                    # contrast wash is confined to the rect itself rather than a full-width band
                    # that could otherwise darken part of the product next to a narrow rect.
                    grx0, gry0, grx1, gry1 = smart_layout.rect
                    gradient_svg = f"""
                      <linearGradient id="overlay-grad" x1="0%" y1="0%" x2="0%" y2="100%">
                        <stop offset="0%" stop-color="rgba({grad_base},0.65)" />
                        <stop offset="60%" stop-color="rgba({grad_base},0.25)" />
                        <stop offset="100%" stop-color="rgba({grad_base},0)" />
                      </linearGradient>
                      <rect x="{grx0}" y="{gry0}" width="{grx1 - grx0}" height="{gry1 - gry0}" fill="url(#overlay-grad)" />
                    """
                else:
                    # Determine gradient direction based on placement
                    is_top = "top" in placement
                    grad_y1, grad_y2 = ("0%", "100%") if is_top else ("100%", "0%")
                    gradient_svg = f"""
                      <linearGradient id="overlay-grad" x1="0%" y1="{grad_y1}" x2="0%" y2="{grad_y2}">
                        <stop offset="0%" stop-color="rgba({grad_base},0.8)" />
                        <stop offset="40%" stop-color="rgba({grad_base},0.4)" />
                        <stop offset="100%" stop-color="rgba({grad_base},0)" />
                      </linearGradient>
                      <!-- We draw a rect across the whole width, covering the third of the image where text is -->
                      <rect x="0" y="{0 if is_top else height - int(height * 0.4)}" width="{width}" height="{int(height * 0.4)}" fill="url(#overlay-grad)" />
                    """

                # Build tspan elements
                tspan_html = ""
                for i, hl in enumerate(headline_lines):
                    dy = 0 if i == 0 else int(final_h_size * 1.2)
                    tspan_html += f'\n<tspan x="{anchor_hx}" dy="{dy}" class="overlay-headline">{hl}</tspan>'
                
                for i, sub in enumerate(subhead_lines):
                    # Gap before the first subhead line accounts for the HEADLINE's own line-
                    # height/descent too (see `subhead_gap` above) — not just the subhead's own
                    # smaller size, which used to crowd a large bold headline into the subhead
                    # right below it.
                    dy = subhead_gap if i == 0 else int(final_s_size * 1.2)
                    if i == 0 and not headline_lines:
                        dy = 0
                    tspan_html += f'\n<tspan x="{anchor_sx}" dy="{dy}" class="overlay-subhead">{sub}</tspan>'

                svg_content = f"""<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
                  <defs>
                    <style>
                      {font_face_rule}
                      .overlay-headline {{
                        font-family: '{font_family}', sans-serif;
                        font-size: {final_h_size}px;
                        font-weight: 800;
                        fill: {text_color_css};
                        letter-spacing: -0.02em;
                      }}
                      .overlay-subhead {{
                        font-family: '{font_family}', sans-serif;
                        font-size: {final_s_size}px;
                        font-weight: 400;
                        fill: {subhead_color_css};
                      }}
                    </style>
                  </defs>
                  {gradient_svg}
                  <text x="0" y="{y + final_h_size}" text-anchor="{text_anchor}">{tspan_html}</text>
                </svg>"""

                png_bytes = cairosvg.svg2png(bytestring=svg_content.encode("utf-8"))
                overlay_img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
                combined = Image.alpha_composite(base, overlay_img).convert("RGB")
                buf = io.BytesIO()
                combined.save(buf, format="JPEG", quality=92)
                res_bytes = buf.getvalue()

                new_ref = await save_asset(
                    res_bytes,
                    "image/jpeg",
                    metadata={"text_overlay": text, "placement": placement, "font_family": font_family, "backend": "svg", "edited_from": storage_ref, "mask_aware_placement": use_smart},
                )
                return ToolResult(ok=True, data={"storage_ref": new_ref, "mime_type": "image/jpeg", "backend": "svg"})
            except Exception:
                import traceback
                traceback.print_exc()
                # Fall back cleanly to Pillow rendering below
        else:
            print("cairosvg not available, falling back to Pillow")

        # Fallback / Pillow backend
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Dynamically choose gradient color based on text luminance for contrast
        luminance = 0.299 * text_color[0] + 0.587 * text_color[1] + 0.114 * text_color[2]
        grad_r, grad_g, grad_b = (255, 255, 255) if luminance < 128 else (0, 0, 0)

        if use_smart:
            # Confine the contrast wash to the chosen rect (which may be a side strip, not a
            # full-width band) instead of assuming a horizontal top/bottom band across the image.
            grx0, gry0, grx1, gry1 = smart_layout.rect
            grad_w, grad_h = max(1, grx1 - grx0), max(1, gry1 - gry0)
            grad_img = Image.new("RGBA", (1, grad_h), color=0)
            for gy in range(grad_h):
                alpha = int(180 * (1.0 - (gy / grad_h)))
                grad_img.putpixel((0, gy), (grad_r, grad_g, grad_b, alpha))
            grad_img = grad_img.resize((grad_w, grad_h))
            overlay.paste(grad_img, (grx0, gry0))
        else:
            # Smooth alpha gradient background
            is_top = "top" in placement
            grad_h = int(height * 0.4)
            grad_img = Image.new("RGBA", (1, grad_h), color=0)
            for gy in range(grad_h):
                alpha = int(220 * (1.0 - (gy / grad_h))) if is_top else int(220 * (gy / grad_h))
                grad_img.putpixel((0, gy), (grad_r, grad_g, grad_b, alpha))
            grad_img = grad_img.resize((width, grad_h))
            overlay.paste(grad_img, (0, 0 if is_top else height - grad_h))
        
        # Draw text
        draw.multiline_text((hx, y), "\n".join(headline_lines), font=font_headline, fill=text_color, align=align)
        if subhead_lines:
            sy = y + h_h + subhead_gap
            draw.multiline_text((sx, sy), "\n".join(subhead_lines), font=font_subhead, fill=subhead_color, align=align)
        
        combined = Image.alpha_composite(base, overlay).convert("RGB")
        buf = io.BytesIO()
        combined.save(buf, format="JPEG", quality=92)
        result_bytes = buf.getvalue()

        new_ref = await save_asset(
            result_bytes,
            "image/jpeg",
            metadata={"text_overlay": text, "placement": placement, "font_family": font_family, "backend": "pillow", "edited_from": storage_ref, "mask_aware_placement": use_smart},
        )
        return ToolResult(ok=True, data={"storage_ref": new_ref, "mime_type": "image/jpeg", "backend": "pillow"})
