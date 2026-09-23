"""
gemini_video.py -- Gemini/Veo video generation provider.

Uses the Gemini REST API (predictLongRunning + operation polling).
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING

import httpx

from ..http_client import get_default_client, get_long_client

if TYPE_CHECKING:
    from ..gemini_key_rotator import APIKeyManager

from .types import VideoGenRequest, VideoSubmitResult, VideoPollResult, VideoItem

log = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"

# Resolutions that require duration_seconds=8 (Veo API constraint)
_HIGH_RES_REQUIRES_8S = frozenset({"1080p", "4k"})


async def submit_gemini_video(
    request: VideoGenRequest,
    model: str,
    key_manager: "APIKeyManager",
) -> VideoSubmitResult:
    """
    Submit a Veo video generation job via Gemini API.
    Returns operation_id for polling.
    """
    from ..retry import execute_with_retry
    from ..creative_studio.exceptions import GeminiError, GeminiRateLimitError, GeminiTimeoutError

    effective_prompt = request.prompt.strip()
    if request.brand_memory and request.brand_memory.strip():
        effective_prompt = (
            f"{effective_prompt}\n\n"
            f"Brand context: {request.brand_memory.strip()}\n"
            "Apply the brand identity to the visual style, lighting, and scene composition."
        )

    instance: dict = {"prompt": effective_prompt}
    if request.image_base64:
        instance["image"] = {
            "bytesBase64Encoded": request.image_base64,
            "mimeType": request.image_mime_type,
        }

    # lastFrame is only supported with a DISTINCT end image and durationSeconds=8.
    # Identical start/end (or missing end) → pure image-to-video animation (no lastFrame).
    use_last_frame = bool(
        request.end_image_base64
        and request.image_base64
        and request.end_image_base64 != request.image_base64
    )
    if use_last_frame:
        instance["lastFrame"] = {
            "bytesBase64Encoded": request.end_image_base64,
            "mimeType": request.end_image_mime_type,
        }

    # FIX: 1080p and 4k require durationSeconds=8 — enforce it silently
    duration = getattr(request, "duration_seconds", 8) or 8
    resolution = getattr(request, "resolution", "720p") or "720p"
    if resolution in _HIGH_RES_REQUIRES_8S and duration != 8:
        log.warning(
            "Gemini video: resolution=%s requires duration_seconds=8, overriding from %ds to 8s",
            resolution, duration,
        )
        duration = 8
    # first+last frame interpolation only works at 8s on Veo 3.1
    if use_last_frame and duration != 8:
        log.warning(
            "Gemini video: lastFrame requires duration_seconds=8, overriding from %ds to 8s",
            duration,
        )
        duration = 8

    payload = {
        "model": f"models/{model}",
        "instances": [instance],
        "parameters": {
            "aspectRatio": request.aspect_ratio,
            "sampleCount": request.sample_count,
            "durationSeconds": duration,
            "resolution": resolution,
        },
    }

    log.info(
        "Gemini video: submit model=%s aspect=%s duration=%s resolution=%s has_image=%s has_last_frame=%s",
        model,
        request.aspect_ratio,
        duration,
        resolution,
        bool(request.image_base64),
        use_last_frame,
    )

    url = f"{GEMINI_BASE_URL}/v1beta/models/{model}:predictLongRunning"

    key_used: list[str | None] = [None]

    async def _do_post(key: str) -> dict:
        key_used[0] = key
        headers = {"Content-Type": "application/json", "x-goog-api-key": key}
        client = get_default_client()
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code == 429:
            body = resp.json() if resp.content else {}
            msg = body.get("error", {}).get("message", "Gemini quota exceeded")
            raise GeminiRateLimitError(msg)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except Exception:
                msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
            raise GeminiError(f"Gemini API error: {msg}")
        return resp.json()

    data = await execute_with_retry(key_manager, _do_post, max_attempts=3)

    operation_id = data.get("name")
    if not operation_id:
        raise GeminiError(
            "Veo API accepted the request but did not return an operation name. "
            f"Response keys: {list(data.keys())}"
        )

    log.info("Gemini video: submit accepted operation=%s model=%s", operation_id, model)
    return VideoSubmitResult(
        operation_id=operation_id,
        provider_used="gemini",
        model_used=model,
        api_key_for_poll=key_used[0],
    )


async def poll_gemini_video(
    operation_id: str,
    key_manager: "APIKeyManager",
    *,
    api_key_for_poll: str | None = None,
) -> VideoPollResult:
    """
    Poll a Gemini/Veo long-running operation.
    Must use the same API key as submit; pass api_key_for_poll from VideoSubmitResult.
    """
    from ..retry import execute_with_retry
    from ..creative_studio.exceptions import GeminiError, GeminiRateLimitError, GeminiTimeoutError

    url = f"{GEMINI_BASE_URL}/v1beta/{operation_id}"

    def _get_key() -> str:
        if api_key_for_poll:
            return api_key_for_poll
        return key_manager.get_key()

    async def _do_get(key: str) -> dict:
        headers = {"Content-Type": "application/json", "x-goog-api-key": key}
        client = get_default_client()
        resp = await client.get(url, headers=headers)
        if resp.status_code == 429:
            body = resp.json() if resp.content else {}
            msg = body.get("error", {}).get("message", "Gemini quota exceeded")
            raise GeminiRateLimitError(msg)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except Exception:
                msg = f"HTTP {resp.status_code}"
            raise GeminiError(f"Gemini poll API error: {msg}")
        return resp.json()

    if api_key_for_poll:
        # Keep affinity to the submit key, but retry transient Gemini outages locally.
        last_exc: Exception | None = None
        data = None
        for attempt in range(1, 4):
            try:
                data = await _do_get(api_key_for_poll)
                last_exc = None
                break
            except GeminiRateLimitError:
                raise
            except (GeminiError, GeminiTimeoutError) as exc:
                last_exc = exc
                msg = str(exc).lower()
                transient = (
                    "unavailable" in msg
                    or "timeout" in msg
                    or "timed out" in msg
                    or "503" in msg
                    or "500" in msg
                    or "502" in msg
                )
                if not transient or attempt >= 3:
                    raise
                await asyncio.sleep(2 * attempt)
        if data is None:
            assert last_exc is not None
            raise last_exc
    else:
        data = await execute_with_retry(key_manager, _do_get, max_attempts=3)

    if not data.get("done", False):
        return VideoPollResult(done=False)

    if "error" in data:
        error_msg = data["error"].get("message", "Veo operation failed without a message.")
        log.warning("Gemini video poll: operation=%s failed: %s", operation_id, error_msg)
        return VideoPollResult(done=True, error=error_msg)

    response = data.get("response", {})
    generate_video_response = response.get("generateVideoResponse", {})

    videos = (
        generate_video_response.get("generatedSamples")
        or generate_video_response.get("videos")
        or generate_video_response.get("predictions")
        or response.get("videos")
        or response.get("predictions")
        or response.get("generatedSamples")
        or response.get("samples")
        or []
    )

    if not videos and response.get("bytesBase64Encoded"):
        videos = [response]

    if not videos:
        return VideoPollResult(
            done=True,
            error=(
                "Operation completed but no video data was found in the response. "
                f"Response keys present: {list(response.keys())}"
            ),
        )

    async def _resolve_video(vid: dict, index: int) -> VideoItem:
        if "video" in vid and isinstance(vid["video"], dict):
            vid = vid["video"]
        b64 = vid.get("bytesBase64Encoded")
        mime = vid.get("mimeType", "video/mp4")
        uri = vid.get("uri", "")
        if not b64 and uri:
            key = _get_key()
            long_client = get_long_client()
            video_resp = await long_client.get(
                uri,
                headers={"x-goog-api-key": key},
                follow_redirects=True,
            )
            if video_resp.status_code != 200:
                raise GeminiError(f"Video[{index}] URI fetch failed: HTTP {video_resp.status_code}")
            b64 = base64.b64encode(video_resp.content).decode("utf-8")
            mime = video_resp.headers.get("content-type", mime).split(";")[0].strip()
        if not b64:
            raise GeminiError(f"Video[{index}] completed but no data or URI found.")
        return VideoItem(data_url=f"data:{mime};base64,{b64}", mime_type=mime)

    resolved = await asyncio.gather(*[_resolve_video(v, i) for i, v in enumerate(videos)])
    log.info("Gemini video: poll complete operation=%s videos=%d", operation_id, len(resolved))
    return VideoPollResult(
        done=True,
        videos=list(resolved),
        data_url=resolved[0].data_url,
        mime_type=resolved[0].mime_type,
    )