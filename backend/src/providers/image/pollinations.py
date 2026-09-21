"""
THE ONLY file that talks to Pollinations.ai — free, keyless image generation.

No API key, no signup. A simple GET against image.pollinations.ai/prompt/<encoded prompt> returns
image bytes directly. Chosen as the primary image-generation provider because the Gemini/Veo
free-tier quota on this project's account is exhausted (Architecture.md section 3).

Retry-with-backoff added after real testing (Memory.md, Phase 2) showed Pollinations wraps its
own upstream rate limit (a shared, community-wide "300 RPM" cap on the underlying free model) as a
generic HTTP 500 rather than a proper 429 — confirmed by inspecting the raw response body, not
guessed. Mirrors the same capped-retry shape already proven in providers/llm/openrouter.py.
"""
from __future__ import annotations

import asyncio
import time
import urllib.parse

import httpx

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from ...core.mime_sniff import sniff_image_mime
from .base import ImageGenProvider, ImageResult

log = get_logger(__name__)

_ASPECT_TO_SIZE = {
    "1:1": (1024, 1024),
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "4:5": (1024, 1280),
    "3:2": (1200, 800),
}


class PollinationsImageProvider(ImageGenProvider):
    def __init__(self, base_url: str | None = None):
        self._base_url = (base_url or settings.pollinations_base_url).rstrip("/")

    async def generate(self, *, prompt: str, aspect_ratio: str = "1:1") -> ImageResult:
        width, height = _ASPECT_TO_SIZE.get(aspect_ratio, _ASPECT_TO_SIZE["1:1"])
        encoded = urllib.parse.quote(prompt, safe="")
        url = f"{self._base_url}/prompt/{encoded}?width={width}&height={height}&nologo=true"

        retries = 3
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            start = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                    resp = await client.get(url)
            except httpx.TimeoutException as exc:
                last_error = ProviderUnavailable("pollinations", f"timeout: {exc}")
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            except httpx.HTTPError as exc:
                raise ProviderUnavailable("pollinations", f"request failed: {exc}") from exc

            # Pollinations' free backend wraps its own upstream 429 (a shared community rate
            # limit) as a generic 500 — retry it the same as a real 429, don't treat it as a
            # hard failure on the first hit.
            transient = resp.status_code == 429 or (
                resp.status_code == 500 and b"429" in resp.content[:500]
            )
            if transient:
                wait = 3.0 * (attempt + 1)
                log.warning(
                    "pollinations_rate_limited",
                    extra={"_extra_attempt": attempt + 1, "_extra_wait_s": wait},
                )
                last_error = ProviderUnavailable("pollinations", "rate limited (community RPM cap)")
                await asyncio.sleep(wait)
                continue

            if resp.status_code != 200 or len(resp.content) < 500:
                raise ProviderUnavailable(
                    "pollinations", f"HTTP {resp.status_code}, {len(resp.content)} bytes"
                )

            declared = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            mime = sniff_image_mime(resp.content, declared)
            log.info(
                "pollinations_generate",
                extra={
                    "_extra_bytes": len(resp.content),
                    "_extra_attempt": attempt + 1,
                    "_extra_ms": round((time.monotonic() - start) * 1000, 1),
                },
            )
            return ImageResult(image_bytes=resp.content, mime_type=mime, provider_name="pollinations")

        raise last_error or ProviderUnavailable("pollinations", "failed after retries")


_singleton: PollinationsImageProvider | None = None


def get_image_gen_provider() -> ImageGenProvider:
    """The active ImageGenProvider — callers depend on this, never the concrete class directly
    (Rules.md section 1: Dependency Inversion). Swapping the active provider is a one-line change
    here, not in every tool that generates images."""
    global _singleton
    if _singleton is None:
        _singleton = PollinationsImageProvider()
    return _singleton
