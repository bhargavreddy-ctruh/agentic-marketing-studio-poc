"""
Replicate image provider — calls alibaba/qwen-image-3 for high quality image generation.
"""
from __future__ import annotations

import asyncio
import base64
import os
import time

import httpx
import replicate

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from ...core.mime_sniff import sniff_image_mime
from .base import ImageGenProvider, ImageEditProvider, ImageResult

log = get_logger(__name__)

class ReplicateImageProvider(ImageGenProvider, ImageEditProvider):
    def __init__(self):
        # We explicitly pass the token from settings because pydantic-settings loads .env
        # but does not inject into os.environ automatically.
        self._client = replicate.Client(api_token=settings.replicate_api_token)

    async def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str = "1:1",
        negative_prompt: str | None = None,
        enable_prompt_expansion: bool = True,
        seed: int | None = None,
        reference_image_bytes: bytes | None = None,
        reference_mime_type: str | None = None,
    ) -> ImageResult:
        start = time.monotonic()
        try:
            # Real, live-found gap (2026-09-24, per an explicit user ask to check every input
            # `readme.md` documents for `alibaba/qwen-image-3` against what the tool-calling agent
            # actually sends): `negative_prompt`/`enable_prompt_expansion`/`seed` were accepted by
            # neither this provider nor the `base_image_generator` tool's schema above it — real,
            # documented model inputs the agent had no way to ever set. Wired through now.
            request_input: dict = {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "enable_prompt_expansion": enable_prompt_expansion,
            }
            if negative_prompt:
                request_input["negative_prompt"] = negative_prompt
            if seed is not None:
                request_input["seed"] = seed
            if reference_image_bytes:
                # Real image-to-image grounding (2026-09-24) — the same real `image` input the
                # edit path already uses, now also reachable from generation, so a referenced
                # element handed to a from-scratch generation step doesn't have to go through a
                # lossy text description of itself first. `aspect_ratio` is still honored
                # alongside a reference image — this is generation with visual grounding, not an
                # edit, so it never implies "keep the reference's own shape" the way editing does.
                b64_data = base64.b64encode(reference_image_bytes).decode("utf-8")
                request_input["image"] = f"data:{reference_mime_type or 'image/png'};base64,{b64_data}"
            output = await self._client.async_run(
                "alibaba/qwen-image-3",
                input=request_input,
            )
            
            # Extract the actual file object from the output
            file_obj = output[0] if isinstance(output, list) and len(output) > 0 else output
            
            # Replicate FileOutput objects have a .read() method and .url property
            if hasattr(file_obj, "read"):
                # It's an IO-like object, read bytes directly
                image_bytes = file_obj.read()
            elif hasattr(file_obj, "url"):
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                    resp = await client.get(file_obj.url)
                    resp.raise_for_status()
                    image_bytes = resp.content
            else:
                # Fallback to assuming it's a raw string URL
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                    resp = await client.get(str(file_obj))
                    resp.raise_for_status()
                    image_bytes = resp.content
                
            # Default to png, though qwen might return webp or jpeg. sniff_image_mime will correct it.
            mime = sniff_image_mime(image_bytes, "image/png")
            
            log.info(
                "replicate_generate",
                extra={
                    "_extra_bytes": len(image_bytes),
                    "_extra_ms": round((time.monotonic() - start) * 1000, 1),
                },
            )
            return ImageResult(image_bytes=image_bytes, mime_type=mime, provider_name="replicate_qwen")
            
        except Exception as exc:
            log.error("replicate_failed", extra={"_extra_error": str(exc)})
            raise ProviderUnavailable("replicate", f"request failed: {exc}") from exc

    async def edit(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        instruction: str,
        aspect_ratio: str | None = None,
        match_input_image: bool = True,
        negative_prompt: str | None = None,
        enable_prompt_expansion: bool = True,
        seed: int | None = None,
    ) -> ImageResult:
        start = time.monotonic()
        try:
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            data_uri = f"data:{mime_type};base64,{b64_data}"

            # Real, live-found bug (2026-09-24, per an explicit user report of "very bad output"
            # on a real edit): this used to send only `prompt`/`image` — none of `aspect_ratio`,
            # `match_input_image`, `negative_prompt`, `enable_prompt_expansion`, or `seed` ever
            # reached the model, despite `readme.md` documenting all of them as real inputs
            # `alibaba/qwen-image-3` accepts for editing. `match_input_image` takes precedence when
            # true (the common case — most edits shouldn't silently reshape the source image);
            # an explicit `aspect_ratio` is only sent when the caller actually wants a DIFFERENT
            # shape than the input (e.g. "make this a 9:16 Instagram post"), which the tool below
            # signals by passing `match_input_image=False` alongside it.
            request_input: dict = {
                "prompt": instruction,
                "image": data_uri,
                "enable_prompt_expansion": enable_prompt_expansion,
            }
            if match_input_image:
                request_input["match_input_image"] = True
            elif aspect_ratio:
                request_input["aspect_ratio"] = aspect_ratio
            if negative_prompt:
                request_input["negative_prompt"] = negative_prompt
            if seed is not None:
                request_input["seed"] = seed

            output = await self._client.async_run(
                "alibaba/qwen-image-3",
                input=request_input,
            )
            
            file_obj = output[0] if isinstance(output, list) and len(output) > 0 else output
            
            if hasattr(file_obj, "read"):
                out_bytes = file_obj.read()
            elif hasattr(file_obj, "url"):
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                    resp = await client.get(file_obj.url)
                    resp.raise_for_status()
                    out_bytes = resp.content
            else:
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                    resp = await client.get(str(file_obj))
                    resp.raise_for_status()
                    out_bytes = resp.content
                
            out_mime = sniff_image_mime(out_bytes, "image/png")
            
            log.info(
                "replicate_edit",
                extra={
                    "_extra_bytes": len(out_bytes),
                    "_extra_ms": round((time.monotonic() - start) * 1000, 1),
                },
            )
            return ImageResult(image_bytes=out_bytes, mime_type=out_mime, provider_name="replicate_qwen")
            
        except Exception as exc:
            log.error("replicate_edit_failed", extra={"_extra_error": str(exc)})
            raise ProviderUnavailable("replicate", f"edit request failed: {exc}") from exc


_singleton: ReplicateImageProvider | None = None

def get_image_gen_provider() -> ImageGenProvider:
    global _singleton
    if _singleton is None:
        _singleton = ReplicateImageProvider()
    return _singleton

def get_image_edit_provider() -> ImageEditProvider:
    global _singleton
    if _singleton is None:
        _singleton = ReplicateImageProvider()
    return _singleton
