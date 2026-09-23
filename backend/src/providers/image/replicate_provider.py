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

    async def generate(self, *, prompt: str, aspect_ratio: str = "1:1") -> ImageResult:
        start = time.monotonic()
        try:
            output = await self._client.async_run(
                "alibaba/qwen-image-3",
                input={
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio
                }
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

    async def edit(self, *, image_bytes: bytes, mime_type: str, instruction: str) -> ImageResult:
        start = time.monotonic()
        try:
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            data_uri = f"data:{mime_type};base64,{b64_data}"
            
            output = await self._client.async_run(
                "alibaba/qwen-image-3",
                input={
                    "prompt": instruction,
                    "image": data_uri
                }
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
