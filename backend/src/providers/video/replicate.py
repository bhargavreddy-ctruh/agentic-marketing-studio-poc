"""
THE ONLY file that talks to Replicate's API — a second video-generation option alongside fal.ai
(Architecture.md's provider-registry pattern: adding this is one new file, zero changes to any
other provider, tool, or specialist).

Added specifically because real testing found fal.ai's account balance exhausted (Memory.md,
Phase 2) — not because fal.ai's own provider code was wrong.

Uses the official `replicate` Python SDK directly (matches Replicate's own documented usage for
this exact model, and the SDK handles uploading local image bytes automatically — no manual
base64 data-URI construction needed, unlike fal.ai's raw-REST provider).

Default model: `bytedance/seedance-2.0-fast` (2026-09-25, replacing `prunaai/p-video` — real,
current schema confirmed live against api.replicate.com/v1/models/bytedance/seedance-2.0-fast, per
`seedance_2.0_fast_replicate_reference.md`). Unlike p-video, this model has REAL `duration`,
`resolution`, and `aspect_ratio` input fields (confirmed enums: resolution in {480p, 720p},
aspect_ratio in {16:9, 4:3, 1:1, 3:4, 9:16, 21:9, 9:21, adaptive}) — what the user asked for is now
honored directly as a request parameter, not indirectly via the input image's own dimensions.
Native synchronized audio generation (`generate_audio`) is now a real, explicit parameter — left at
the model's own default (on) when the caller doesn't say, but controllable when the chosen video
model's native audio is NOT what's wanted (e.g. a scripted voiceover will be muxed in separately).

`model` can also select Google Veo (confirmed live-available on Replicate as of late 2025/2026, per
third-party reseller/pricing pages — but the EXACT model slug and its own valid resolution/
aspect_ratio/audio schema have NOT been independently re-verified against Replicate's own
`/v1/models` endpoint from this environment, the same "don't trust an unverified name" lesson
`core/config.py` already states for Groq/OpenRouter. The Seedance-specific resolution/aspect_ratio
clamp below is therefore only applied when the Seedance model is actually selected — a non-Seedance
model's own values are passed through as given rather than silently forced into Seedance's narrower
set, but re-verify Veo's real schema before relying on this in production.
"""
from __future__ import annotations

import asyncio
import io
import time

import httpx
import replicate as replicate_sdk

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import VideoGenProvider, VideoResult

log = get_logger(__name__)

_VALID_RESOLUTIONS = {"480p", "720p"}
_VALID_ASPECT_RATIOS = {"16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "9:21", "adaptive"}


class ReplicateVideoProvider(VideoGenProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or settings.replicate_api_token
        self._model = model or settings.replicate_model

    async def generate(
        self,
        *,
        prompt: str,
        image_bytes: bytes | None = None,
        duration_seconds: int = 5,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        camera_motion: str | None = None,
        first_frame_bytes: bytes | None = None,
        last_frame_bytes: bytes | None = None,
        audio_bytes: bytes | None = None,
        model: str | None = None,
        generate_audio: bool | None = None,
    ) -> VideoResult:
        if not self._api_key:
            raise ProviderUnavailable("replicate", "REPLICATE_API_KEY is not set")

        effective_image_bytes = image_bytes or first_frame_bytes
        if effective_image_bytes is None:
            raise ProviderUnavailable("replicate", "Video provider requires a source image or first frame")

        effective_prompt = prompt
        if camera_motion and camera_motion.lower() not in prompt.lower():
            effective_prompt = f"{prompt}. Camera motion: {camera_motion}."

        client = replicate_sdk.Client(
            api_token=self._api_key,
            timeout=httpx.Timeout(600.0)
        )

        # Real vendor constraint (confirmed via the model's own schema): duration is -1 (model
        # picks) or an integer 1-15; anything outside that range would be rejected by the API, so
        # clamp defensively rather than let an upstream caller's arbitrary value 400.
        effective_duration = duration_seconds if duration_seconds == -1 else max(1, min(15, duration_seconds))
        effective_model = model or self._model

        # The resolution/aspect_ratio validation below is Seedance-specific (confirmed live against
        # its own schema) — only applied when Seedance is actually the chosen model, so selecting a
        # different model (e.g. Veo) isn't silently forced into Seedance's narrower set.
        if effective_model == self._model:
            effective_resolution = resolution if resolution in _VALID_RESOLUTIONS else "720p"
            effective_aspect_ratio = aspect_ratio if aspect_ratio in _VALID_ASPECT_RATIOS else "16:9"
            if resolution not in _VALID_RESOLUTIONS or aspect_ratio not in _VALID_ASPECT_RATIOS:
                log.warning(
                    "replicate_invalid_video_param_fallback",
                    extra={
                        "_extra_requested_resolution": resolution,
                        "_extra_requested_aspect_ratio": aspect_ratio,
                        "_extra_used_resolution": effective_resolution,
                        "_extra_used_aspect_ratio": effective_aspect_ratio,
                    },
                )
        else:
            effective_resolution = resolution
            effective_aspect_ratio = aspect_ratio

        input_payload = {
            "image": io.BytesIO(effective_image_bytes),
            "prompt": effective_prompt,
            "duration": effective_duration,
            "resolution": effective_resolution,
            "aspect_ratio": effective_aspect_ratio,
        }
        if last_frame_bytes:
            input_payload["last_frame_image"] = io.BytesIO(last_frame_bytes)
        if audio_bytes:
            input_payload["audio"] = io.BytesIO(audio_bytes)
        if generate_audio is not None:
            input_payload["generate_audio"] = generate_audio

        start = time.monotonic()
        try:
            model_info = await client.models.async_get(effective_model)
            version = model_info.latest_version

            prediction = await client.predictions.async_create(
                version=version,
                input=input_payload,
            )

            attempts = 0
            max_attempts = 300  # 10 minutes max (at 2s per poll)
            while prediction.status not in ["succeeded", "failed", "canceled"]:
                if attempts >= max_attempts:
                    raise ProviderUnavailable("replicate", f"Prediction timed out after {max_attempts} attempts.")
                await asyncio.sleep(2)
                prediction = await client.predictions.async_get(prediction.id)
                attempts += 1

            if prediction.status != "succeeded":
                raise ProviderUnavailable("replicate", f"Prediction ended with status: {prediction.status}")

            output = prediction.output
        except Exception as exc:  # the replicate SDK raises its own exception types — none escape this file
            raise ProviderUnavailable("replicate", str(exc)) from exc

        try:
            video_bytes = output.read()
        except AttributeError:
            # Some model/SDK versions return a plain URL string instead of a FileOutput object.
            # Real, live-found inconsistency (2026-09-30): this stayed at 60s while the image
            # provider's own equivalent download call was already raised to 300s — a genuinely
            # large rendered video file can take longer than 60s to download even after
            # generation itself already succeeded, needlessly failing an otherwise-complete
            # render. Matches `providers/image/replicate_provider.py`'s own 300s value.
            async with httpx.AsyncClient(timeout=300, follow_redirects=True) as http_client:
                resp = await http_client.get(str(output))
                video_bytes = resp.content

        log.info(
            "replicate_generate",
            extra={
                "_extra_model": effective_model,
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


def reset_video_provider() -> None:
    """See llm/router.py's reset_llm_provider() docstring — same pattern, for
    replicate_api_token."""
    global _singleton
    _singleton = None
    return _singleton
