"""
QwenEditPlusProvider — calls `qwen/qwen-image-edit-plus` on Replicate, the ONLY file that imports
this specific vendor model (Rules.md section 1: one provider, one file — swapping to a different
multi-image model later, e.g. `bytedance/seedream-4`, is a new provider file, not a rewrite of
this one).

Real, live-found gap this exists to close (2026-09-26): `alibaba/qwen-image-3`
(`replicate_provider.py`) only ever takes ONE reference image (`image` is a plain string in its
real schema) — it cannot combine a brand logo with a product photo in one generation at all.
`qwen/qwen-image-edit-plus`'s real schema (confirmed live against api.replicate.com, not assumed)
has `image` as an ARRAY of reference image URIs — genuine multi-reference support. Used
exclusively by the `collab_image_generator` tool, for the specific case of combining 2+ real,
distinct visual assets — never the default single-reference path (`base_image_generator` covers
that, unchanged).
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

_MODEL = "qwen/qwen-image-edit-plus"


class QwenEditPlusProvider:
    def __init__(self):
        self._client = replicate.Client(
            api_token=settings.replicate_api_token,
            timeout=httpx.Timeout(600.0),
        )

    async def generate(
        self,
        *,
        prompt: str,
        reference_images: list[tuple[bytes, str]],
        aspect_ratio: str | None = None,
        negative_prompt: str | None = None,
        seed: int | None = None,
    ) -> ImageResult:
        """`reference_images`: 2+ real (bytes, mime_type) pairs — e.g. a brand/sponsor logo and
        the product's own correctly-resolved photo (never a stale or guessed-at one — the caller
        is responsible for that, same discipline as every other reference resolution in this
        app). Raises `ProviderUnavailable` if fewer than one is given; genuinely fewer than 2
        defeats the point of this tool but isn't rejected here — that judgment belongs to the
        specialist deciding whether to call this tool at all, not this provider."""
        if not reference_images:
            raise ProviderUnavailable(_MODEL, "at least one reference image is required")
        start = time.monotonic()
        try:
            request_input: dict = {
                "prompt": prompt,
                "image": [
                    f"data:{mime_type};base64,{base64.b64encode(data).decode('utf-8')}"
                    for data, mime_type in reference_images
                ],
            }
            if aspect_ratio:
                request_input["aspect_ratio"] = aspect_ratio
            if negative_prompt:
                request_input["negative_prompt"] = negative_prompt
            if seed is not None:
                request_input["seed"] = seed

            # Same explicit create-and-poll pattern as `replicate_provider.py` — avoids dropping
            # the connection/timing out early and having the calling LLM think the call failed.
            model = await self._client.models.async_get(_MODEL)
            version = model.latest_version

            prediction = await self._client.predictions.async_create(
                version=version,
                input=request_input,
            )

            while prediction.status not in ["succeeded", "failed", "canceled"]:
                await asyncio.sleep(2)
                prediction = await self._client.predictions.async_get(prediction.id)

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

            mime = sniff_image_mime(image_bytes, "image/webp")

            log.info(
                "qwen_edit_plus_generate",
                extra={
                    "_extra_bytes": len(image_bytes),
                    "_extra_reference_count": len(reference_images),
                    "_extra_ms": round((time.monotonic() - start) * 1000, 1),
                },
            )
            return ImageResult(image_bytes=image_bytes, mime_type=mime, provider_name="replicate_qwen_edit_plus")

        except Exception as exc:
            log.error("qwen_edit_plus_failed", extra={"_extra_error": str(exc)})
            raise ProviderUnavailable(_MODEL, f"request failed: {exc}") from exc


_singleton: QwenEditPlusProvider | None = None


def get_collab_image_provider() -> QwenEditPlusProvider:
    global _singleton
    if _singleton is None:
        _singleton = QwenEditPlusProvider()
    return _singleton
