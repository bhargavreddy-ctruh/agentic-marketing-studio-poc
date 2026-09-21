"""
THE ONLY file that talks to Replicate's API — a second video-generation option alongside fal.ai
(Architecture.md's provider-registry pattern: adding this is one new file, zero changes to any
other provider, tool, or specialist).

Added specifically because real testing found fal.ai's account balance exhausted (Memory.md,
Phase 2) — not because fal.ai's own provider code was wrong.

Uses the official `replicate` Python SDK directly (matches Replicate's own documented usage for
this exact model, and the SDK handles uploading local image bytes automatically — no manual
base64 data-URI construction needed, unlike fal.ai's raw-REST provider).

Default model: `prunaai/p-video` — a genuinely light, fast model ("generates a video in under 10
seconds" per its own description), chosen specifically for cheap/quick testing, per the user's own
example of how to call it. Its real, confirmed input schema is exactly
`{image, prompt, prompt_upsampling}` — no aspect_ratio/resolution/duration fields exist on this
model. Orientation and resolution for THIS model are therefore controlled by the aspect ratio and
size of the INPUT IMAGE itself (it's an image-to-video model that animates the given frame), not by
a request parameter — "what the user asked for" is honored upstream, in how the source image is
generated, not here.
"""
from __future__ import annotations

import io
import time

import httpx
import replicate as replicate_sdk

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import VideoGenProvider, VideoResult

log = get_logger(__name__)


class ReplicateVideoProvider(VideoGenProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or settings.replicate_api_key
        self._model = model or settings.replicate_model

    async def generate(
        self,
        *,
        prompt: str,
        image_bytes: bytes | None = None,
        duration_seconds: int = 5,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
    ) -> VideoResult:
        if not self._api_key:
            raise ProviderUnavailable("replicate", "REPLICATE_API_KEY is not set")
        if image_bytes is None:
            raise ProviderUnavailable("replicate", "prunaai/p-video requires a source image")

        log.info(
            "replicate_orientation_note",
            extra={
                "_extra_requested_aspect_ratio": aspect_ratio,
                "_extra_requested_resolution": resolution,
                "_extra_note": "controlled via the input image's own dimensions on this model, not a request field",
            },
        )

        client = replicate_sdk.Client(api_token=self._api_key)
        input_payload = {
            "image": io.BytesIO(image_bytes),
            "prompt": prompt,
            "prompt_upsampling": False,  # keep the exact prompt as given — don't let the model rewrite it
        }

        start = time.monotonic()
        try:
            output = await client.async_run(self._model, input=input_payload)
        except Exception as exc:  # the replicate SDK raises its own exception types — none escape this file
            raise ProviderUnavailable("replicate", str(exc)) from exc

        try:
            video_bytes = output.read()
        except AttributeError:
            # Some model/SDK versions return a plain URL string instead of a FileOutput object.
            async with httpx.AsyncClient(timeout=60) as http_client:
                resp = await http_client.get(str(output))
                video_bytes = resp.content

        log.info(
            "replicate_generate",
            extra={
                "_extra_model": self._model,
                "_extra_bytes": len(video_bytes),
                "_extra_ms": round((time.monotonic() - start) * 1000, 1),
            },
        )
        return VideoResult(
            video_bytes=video_bytes,
            mime_type="video/mp4",
            provider_name="replicate",
            duration_seconds=duration_seconds,
        )


_singleton: ReplicateVideoProvider | None = None


def get_video_provider() -> VideoGenProvider:
    """The active VideoGenProvider — callers depend on this, never the concrete class directly
    (Rules.md section 1: Dependency Inversion). fal.ai stays a registered provider file but isn't
    wired here (its key came back revoked on live testing — Memory.md, Phase 2); pointing this at
    a different provider later is a one-line change here, not in every tool that generates video."""
    global _singleton
    if _singleton is None:
        _singleton = ReplicateVideoProvider()
    return _singleton
