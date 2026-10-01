"""
NanoBanana2Provider — calls `google/nano-banana-2` on Replicate, the ONLY file that imports this
specific vendor model (Rules.md section 1: one provider, one file).

Explicit user ask (2026-10-01): add Nano Banana 2 (the full, higher-fidelity sibling of Nano
Banana 2 Lite — `nano_banana_provider.py`) specifically for when a request genuinely needs 2K/4K
output or the highest achievable fidelity, not as a default/cheap path. Confirmed live against
replicate.com/google/nano-banana-2: model slug `google/nano-banana-2`, same core capabilities as
the Lite tier (text-to-image, editing, up to 14 reference images) plus higher fidelity, better text
rendering, and multiple output resolutions (512px/1K/2K/4K) the Lite tier doesn't offer.

Real web grounding — confirmed directly against the real OpenAPI schema (the user pasted it): two
distinct, genuine boolean fields, not guessed. `google_search` grounds generation in real-time
information (e.g. weather, sports scores, recent events) via Google Web Search. `image_search` uses
Google Image Search to find real web images as visual context, and automatically also enables web
search. Both default to `False` — never enabled unless the caller explicitly asks for them, since
grounding only makes sense when the request genuinely needs current/real-world visual or factual
context, not for an ordinary generation.

Confirmed live (replicate.com/google/nano-banana-2's own "Aspect ratios" section specifically,
not the separate "key improvements" marketing bullet which lists a broader, possibly aspirational
set) — this model's real supported aspect ratios are narrower than its Lite sibling's: no
1:4/4:1/1:8/8:1 here.
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

_MODEL = "google/nano-banana-2"

# Confirmed live, this model's own "Aspect ratios" section — narrower than Lite's (no 1:4/4:1/
# 1:8/8:1 family here).
_VALID_ASPECT_RATIOS = {
    "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "match_input_image",
}
_VALID_RESOLUTIONS = {"512px", "1K", "2K", "4K"}


class NanoBanana2Provider:
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
        resolution: str = "2K",
        output_format: str = "jpg",
        seed: int | None = None,
        google_search: bool = False,
        image_search: bool = False,
    ) -> ImageResult:
        """`reference_images`: zero (pure text-to-image), one, or up to 14 real (bytes,
        mime_type) pairs for editing/combining — same real flexibility as the Lite sibling.
        `resolution` defaults to "2K" here (the real API's own default is "1K" — confirmed via
        the real schema) since the entire point of reaching for THIS provider over the Lite one
        is higher resolution/fidelity; a caller that only needs 1K should use
        `nano_banana_provider.py` instead, which is faster and cheaper. `google_search`/
        `image_search`: real grounding — only set True when the caller explicitly needs
        real-time/current information or real web images as context (see the tool's own
        docstring); never enabled by default."""
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
            if resolution in _VALID_RESOLUTIONS:
                request_input["resolution"] = resolution
            if seed is not None:
                request_input["seed"] = seed
            if google_search:
                request_input["google_search"] = True
            if image_search:
                request_input["image_search"] = True

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

            mime = sniff_image_mime(image_bytes, "image/jpeg")

            log.info(
                "nano_banana_2_generate",
                extra={
                    "_extra_bytes": len(image_bytes),
                    "_extra_resolution": resolution,
                    "_extra_reference_count": len(reference_images or []),
                    "_extra_ms": round((time.monotonic() - start) * 1000, 1),
                },
            )
            return ImageResult(image_bytes=image_bytes, mime_type=mime, provider_name="replicate_nano_banana_2")

        except Exception as exc:
            log.error("nano_banana_2_failed", extra={"_extra_error": str(exc)})
            raise ProviderUnavailable(_MODEL, f"request failed: {exc}") from exc


_singleton: NanoBanana2Provider | None = None


def get_nano_banana_2_provider() -> NanoBanana2Provider:
    global _singleton
    if _singleton is None:
        _singleton = NanoBanana2Provider()
    return _singleton
