"""
Shared "give the model the real image WITH its JSON metadata, never JSON alone" helper (Fix 8 of
the 2026-09-26 image/video quality investigation).

Real, live-found gap: only one of three places that reason about "what is this referenced
element" (`graph.py`'s `_dynamic_executor_node`) ever attached the real image alongside an
element's text description — `_direct_fix_node` (the route used for "fix/edit this specific
element") and `orchestrator.py`'s routing classifier (the very first "what is this turn about"
decision) both reasoned from JSON/text alone, with no image ever attached. A text description can
be stale, wrong, or (per `core/element_descriptions.py`) an outright placeholder — the same class
of anti-pattern a real multimodal assistant (Claude/Gemini/GPT chat) never has, since it can
always see the actual picture. This is the one shared implementation all three call sites use.

Cost discipline: images are downscaled to a small thumbnail before being attached — vision token
cost scales with resolution, and a thumbnail is entirely sufficient for "what is this / is this
the same element," as opposed to the full-resolution asset a GENERATION call needs. Callers should
only pass the specific element(s) actually relevant to their call, never a large candidate list.
"""
from __future__ import annotations

import base64
import io
from typing import Any

from .element_descriptions import NO_DESCRIPTION_SENTINEL
from .local_storage import load_asset
from .middleware.logging import get_logger

log = get_logger(__name__)

_THUMBNAIL_MAX_DIM = 512


def _downscale_thumbnail(image_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Resizes to at most `_THUMBNAIL_MAX_DIM` on the long edge. Any decode/processing failure
    falls back to the original bytes/mime rather than dropping the image entirely — full-res at
    higher cost beats no image at all."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            img.thumbnail((_THUMBNAIL_MAX_DIM, _THUMBNAIL_MAX_DIM))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=80)
            return out.getvalue(), "image/jpeg"
    except Exception as exc:  # best-effort downscale, never blocks reasoning
        log.warning("element_thumbnail_downscale_failed", extra={"_extra_error": str(exc)})
        return image_bytes, mime_type


def build_element_context_blocks(
    element: dict[str, Any], *, thumbnail: bool = True
) -> list[dict[str, Any]]:
    """Builds ONE element's context as a content-block list: a text block with its real JSON
    metadata, followed by a real image block WHEN one exists for it. `element` is one entry of
    `referenced_elements_context` (or an equivalent dict: `storage_ref`, `element_type`,
    `description`).

    Rule for every caller: never reason about an element from this text block alone when it has a
    real image — always include the returned image block too. For a non-image element type
    (video/audio/text), no image block is attached (none exists to attach) — the text says so
    honestly rather than pretending text-only is equivalent.
    """
    ref = element.get("storage_ref")
    kind = element.get("element_type", "unknown")
    desc = element.get("description") or NO_DESCRIPTION_SENTINEL
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": f"Element (storage_ref: {ref}, type: {kind}) depicts:\n{desc}"}
    ]
    if kind == "image" and ref:
        loaded = load_asset(ref)
        if loaded is not None:
            image_bytes, mime_type = loaded
            if thumbnail:
                image_bytes, mime_type = _downscale_thumbnail(image_bytes, mime_type)
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            blocks.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_img}"}}
            )
    return blocks
