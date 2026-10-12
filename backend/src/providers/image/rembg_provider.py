"""
RembgProvider — the ONLY file that imports the `rembg` SDK. Wraps U2-NetP, a tiny background-
removal ONNX model that runs entirely on CPU with no external API call and no per-call cost
(contrast with every other provider in this directory, which calls a paid vendor endpoint).

Produces a product alpha mask consumed by `services/canvas/smart_text_placement.py`, so the
`text_overlay` tool's mask-aware "auto" placement can guarantee overlay text never lands on top
of the product instead of guessing from edge density.
"""
from __future__ import annotations

import asyncio
import io
import time

from PIL import Image

from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import SegmentationResult

log = get_logger(__name__)

_MODEL_NAME = "u2netp"


def _run_remove(image_bytes: bytes) -> bytes:
    """Synchronous and CPU-bound (ONNX Runtime has no asyncio story of its own) — always called
    via `asyncio.to_thread` below so one segmentation never blocks the event loop."""
    import rembg  # the only import of this SDK anywhere in the app

    session = rembg.new_session(_MODEL_NAME)
    with Image.open(io.BytesIO(image_bytes)) as opened:
        cutout = rembg.remove(opened.convert("RGB"), session=session)
    alpha = cutout.split()[-1]
    buf = io.BytesIO()
    alpha.save(buf, format="PNG")
    return buf.getvalue()


class RembgProvider:
    async def remove_background(self, *, image_bytes: bytes, mime_type: str) -> SegmentationResult:
        start = time.monotonic()
        try:
            mask_bytes = await asyncio.to_thread(_run_remove, image_bytes)
        except Exception as exc:
            log.error("rembg_segmentation_failed", extra={"_extra_error": str(exc)})
            raise ProviderUnavailable(_MODEL_NAME, f"segmentation failed: {exc}") from exc

        log.info(
            "rembg_segmentation",
            extra={
                "_extra_ms": round((time.monotonic() - start) * 1000, 1),
                "_extra_bytes_in": len(image_bytes),
            },
        )
        return SegmentationResult(mask_bytes=mask_bytes, mime_type="image/png", provider_name="rembg_u2netp")


_singleton: RembgProvider | None = None


def get_background_removal_provider() -> RembgProvider:
    global _singleton
    if _singleton is None:
        _singleton = RembgProvider()
    return _singleton
