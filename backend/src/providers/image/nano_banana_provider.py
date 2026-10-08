"""
NanoBananaProvider — calls `google/nano-banana-2-lite` on Replicate, the ONLY file that imports
this specific vendor model (Rules.md section 1: one provider, one file).

Explicit user ask (2026-10-01): add Nano Banana 2 Lite — Google's fast, low-cost image model
(built on Gemini 3.1 Flash-Lite Image) — specifically for photorealistic results. Confirmed live
against replicate.com/google/nano-banana-2-lite: model slug `google/nano-banana-2-lite`, supports
pure text-to-image (no references) AND image editing/combining with up to 14 real reference
images in one call — genuinely more flexible than either `replicate_provider.py` (single
reference only) or `qwen_edit_plus_provider.py` (requires 2+ references), so this file accepts
zero, one, or many reference images rather than enforcing either extreme.

Real vendor tradeoff, stated plainly so a caller can judge when to pick this over the alibaba/qwen
providers: 1K output only (no 2K/4K — use `google/nano-banana-pro` for that, not wired up here),
no Google Search/Image Search grounding — but faster and cheaper, which is exactly the "quick,
low-cost photorealistic result" niche this provider exists to fill.
"""
from __future__ import annotations

import asyncio
import base64
import time

import httpx
import replicate

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from ...core.mime_sniff import sniff_image_mime
from .base import ImageResult

log = get_logger(__name__)

_MODEL = "google/nano-banana-2-lite"

# Confirmed live (replicate.com/google/nano-banana-2-lite): the model's own supported set —
# passing anything outside this list is a real vendor-side 422, not a guess worth risking.
_VALID_ASPECT_RATIOS = {
    "1:1", "1:4", "1:8", "2:3", "3:2", "3:4", "4:1", "4:3", "4:5", "5:4",
    "8:1", "9:16", "16:9", "21:9", "match_input_image",
}


class NanoBananaProvider:
    def __init__(self):
        self._client = replicate.Client(
            api_token=settings.replicate_api_token,
            timeout=httpx.Timeout(600.0),
        )

    async def generate(
        self,
        *,
        prompt: str,
        reference_images: list[tuple[bytes, str]] | None = None,
        aspect_ratio: str | None = None,
        output_format: str = "jpg",
        seed: int | None = None,
    ) -> ImageResult:
        """`reference_images`: zero (pure text-to-image), one, or up to 14 real (bytes,
        mime_type) pairs for editing/combining — the vendor's own real flexibility, not
        artificially narrowed to match a sibling provider's own stricter contract."""
        start = time.monotonic()
        try:
            request_input: dict = {"prompt": prompt, "output_format": output_format}
            if reference_images:
                request_input["image_input"] = [
                    f"data:{mime_type};base64,{base64.b64encode(data).decode('utf-8')}"
                    for data, mime_type in reference_images
                ]
            if aspect_ratio and aspect_ratio in _VALID_ASPECT_RATIOS:
                request_input["aspect_ratio"] = aspect_ratio
            if seed is not None:
                request_input["seed"] = seed

            # Same explicit create-and-poll pattern as replicate_provider.py/
            # qwen_edit_plus_provider.py — avoids dropping the connection or timing out early and
            # having the calling LLM think the call failed.
            model = await self._client.models.async_get(_MODEL)
            version = model.latest_version

            prediction = await self._client.predictions.async_create(
                version=version,
                input=request_input,
            )

            attempts = 0
            max_attempts = 150
            while prediction.status not in ["succeeded", "failed", "canceled"]:
                if attempts >= max_attempts:
                    raise ProviderUnavailable(_MODEL, f"Prediction timed out after {max_attempts} attempts.")
                await asyncio.sleep(2)
                prediction = await self._client.predictions.async_get(prediction.id)
                attempts += 1

            if prediction.status != "succeeded":
                raise ProviderUnavailable(_MODEL, f"Prediction ended with status: {prediction.status}")

            output = prediction.output
            file_obj = output[0] if isinstance(output, list) and len(output) > 0 else output

            if hasattr(file_obj, "read"):
                image_bytes = file_obj.read()
            elif hasattr(file_obj, "url"):
                async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
                    resp = await client.get(file_obj.url)
                    resp.raise_for_status()
                    image_bytes = resp.content
            else:
                async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
                    resp = await client.get(str(file_obj))
                    resp.raise_for_status()
                    image_bytes = resp.content

            mime = sniff_image_mime(image_bytes, "image/jpeg")

            log.info(
                "nano_banana_generate",
                extra={
                    "_extra_bytes": len(image_bytes),
                    "_extra_reference_count": len(reference_images or []),
                    "_extra_ms": round((time.monotonic() - start) * 1000, 1),
                },
            )
            return ImageResult(image_bytes=image_bytes, mime_type=mime, provider_name="replicate_nano_banana_2_lite")

        except Exception as exc:
            log.error("nano_banana_failed", extra={"_extra_error": str(exc)})
            raise ProviderUnavailable(_MODEL, f"request failed: {exc}") from exc


_singleton: NanoBananaProvider | None = None


def get_nano_banana_provider() -> NanoBananaProvider:
    global _singleton
    if _singleton is None:
        _singleton = NanoBananaProvider()
    return _singleton
