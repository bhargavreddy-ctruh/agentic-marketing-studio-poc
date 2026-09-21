"""
THE ONLY file that talks to fal.ai — used for video generation on free trial credits
(Architecture.md section 3).

fal.ai's queue API is submit-then-poll: POST to enqueue, then GET the status URL until it
completes. The poll-and-give-up-gracefully pattern (bounded attempts, not infinite) is a direct
port of the existing agentic_flow codebase's video polling logic (Rules.md section 4).
"""
from __future__ import annotations

import asyncio
import base64
import time

import httpx

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import VideoGenProvider, VideoResult

log = get_logger(__name__)


class FalAiVideoProvider(VideoGenProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or settings.falai_api_key
        self._model = model or settings.falai_model
        self._base_url = settings.falai_base_url.rstrip("/")

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
            raise ProviderUnavailable("falai", "FALAI_API_KEY is not set")

        headers = {"Authorization": f"Key {self._api_key}", "Content-Type": "application/json"}
        payload: dict = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "duration": str(duration_seconds),
        }
        if image_bytes is not None:
            payload["image_url"] = (
                f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode()}"
            )
        # `resolution` is deliberately NOT sent as a request field here — Kling-family models on
        # fal.ai tie output quality to the model *path* (e.g. "standard" vs "pro"), not a request
        # parameter, so passing an arbitrary resolution string would silently do nothing rather
        # than actually control it. It's still logged below so it's visible which resolution was
        # asked for, and honored properly once `falai_model` points at a resolution-specific
        # model variant (a config change, not a code change, per Architecture.md section 4).
        log.info(
            "falai_request_params",
            extra={
                "_extra_aspect_ratio": aspect_ratio,
                "_extra_resolution": resolution,
                "_extra_duration_seconds": duration_seconds,
            },
        )

        submit_url = f"{self._base_url}/{self._model}"
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(submit_url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise ProviderUnavailable("falai", f"submit failed: {exc}") from exc

            if resp.status_code >= 400:
                raise ProviderUnavailable("falai", f"HTTP {resp.status_code}: {resp.text[:300]}")

            job = resp.json()
            status_url = job.get("status_url")
            response_url = job.get("response_url")
            if not status_url or not response_url:
                raise ProviderUnavailable("falai", "no status/response URL in submit response")

            for attempt in range(1, settings.falai_max_poll_attempts + 1):
                await asyncio.sleep(settings.falai_poll_interval_seconds)
                try:
                    status_resp = await client.get(status_url, headers=headers)
                except httpx.HTTPError as exc:
                    log.warning("falai_poll_error", extra={"_extra_attempt": attempt, "_extra_error": str(exc)})
                    continue

                status = (status_resp.json() or {}).get("status")
                if status == "COMPLETED":
                    result_resp = await client.get(response_url, headers=headers)
                    result = result_resp.json()
                    video_url = ((result.get("video") or {}).get("url"))
                    if not video_url:
                        raise ProviderUnavailable("falai", "completed but no video URL returned")
                    video_resp = await client.get(video_url)
                    log.info(
                        "falai_generate",
                        extra={
                            "_extra_bytes": len(video_resp.content),
                            "_extra_ms": round((time.monotonic() - start) * 1000, 1),
                        },
                    )
                    return VideoResult(
                        video_bytes=video_resp.content,
                        mime_type="video/mp4",
                        provider_name="falai",
                        duration_seconds=duration_seconds,
                    )
                if status in ("ERROR", "FAILED"):
                    raise ProviderUnavailable("falai", f"generation failed: status={status}")
                # IN_QUEUE / IN_PROGRESS — keep polling, bounded by max_poll_attempts.

        raise ProviderUnavailable(
            "falai", f"timed out after {settings.falai_max_poll_attempts} poll attempts"
        )
