"""
Product Compositor — Removes background from product subject images and composites them onto generated backgrounds.
"""
from __future__ import annotations

import io
from PIL import Image, ImageFilter

from ...core.local_storage import load_asset, save_asset
from ...core.middleware.logging import get_logger

log = get_logger(__name__)


def remove_background(image_bytes: bytes) -> bytes:
    """Removes background from image bytes using rembg if available, otherwise simple alpha thresholding."""
    try:
        import rembg
        return rembg.remove(image_bytes)
    except Exception as exc:
        log.warning("rembg_unavailable_using_pil_fallback", extra={"_extra_error": str(exc)})
        # Fallback using PIL: simple light/dark background transparency mask
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        datas = img.getdata()
        new_data = []
        for item in datas:
            if (item[0] > 240 and item[1] > 240 and item[2] > 240) or (item[0] < 15 and item[1] < 15 and item[2] < 15):
                new_data.append((255, 255, 255, 0))
            else:
                new_data.append(item)
        img.putdata(new_data)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def composite_product_onto_background(
    product_bytes: bytes,
    background_bytes: bytes,
    scale_pct: float = 40.0,
    position: str = "center",
) -> bytes:
    """Composites product image onto background image with drop-shadow."""
    clean_product_bytes = remove_background(product_bytes)
    product_img = Image.open(io.BytesIO(clean_product_bytes)).convert("RGBA")
    bg_img = Image.open(io.BytesIO(background_bytes)).convert("RGBA")

    bg_w, bg_h = bg_img.size

    target_w = max(20, int(bg_w * (scale_pct / 100.0)))
    aspect = product_img.height / max(1, product_img.width)
    target_h = max(20, int(target_w * aspect))
    product_resized = product_img.resize((target_w, target_h), Image.LANCZOS)

    if position == "lower_center":
        x = (bg_w - target_w) // 2
        y = int(bg_h * 0.5)
    elif position == "bottom_center":
        x = (bg_w - target_w) // 2
        y = bg_h - target_h - int(bg_h * 0.05)
    else:  # center
        x = (bg_w - target_w) // 2
        y = (bg_h - target_h) // 2

    shadow = Image.new("RGBA", (target_w + 20, target_h + 20), (0, 0, 0, 0))
    alpha_mask = product_resized.split()[3]
    shadow_mask = alpha_mask.filter(ImageFilter.GaussianBlur(radius=8))
    shadow.paste((0, 0, 0, 120), (10, 10), shadow_mask)

    comp = Image.new("RGBA", bg_img.size, (0, 0, 0, 0))
    comp.paste(shadow, (x - 10, y - 5), shadow)
    comp.paste(product_resized, (x, y), product_resized)

    final = Image.alpha_composite(bg_img, comp).convert("RGB")
    buf = io.BytesIO()
    final.save(buf, format="JPEG", quality=92)
    return buf.getvalue()
