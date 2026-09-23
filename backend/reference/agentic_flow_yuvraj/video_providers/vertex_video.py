"""
vertex_video.py -- Vertex AI Veo video generation provider.

Uses Vertex AI predictLongRunning + fetchPredictOperation for submit and poll.
Auth via GOOGLE_APPLICATION_CREDENTIALS (application default credentials).
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import TYPE_CHECKING

import httpx

from ..http_client import get_default_client, get_long_client

if TYPE_CHECKING:
    from ..gemini_key_rotator import APIKeyManager

from .types import VideoGenRequest, VideoSubmitResult, VideoPollResult, VideoItem

log = logging.getLogger(__name__)

# Vertex Veo accepts only JPEG and PNG for image/lastFrame
_VEO_SUPPORTED_MIME = frozenset({"image/jpeg", "image/jpg", "image/png"})

# Resolutions that require duration_seconds=8 (Veo API constraint)
_HIGH_RES_REQUIRES_8S = frozenset({"1080p", "4k"})


def _ensure_veo_compatible(b64: str, mime_type: str) -> tuple[str, str]:
    """Convert image to JPEG if format not supported by Veo (e.g. WebP). Returns (b64, mime_type)."""
    mime = (mime_type or "").lower().strip()
    if mime in _VEO_SUPPORTED_MIME:
        return b64, mime or "image/jpeg"
    try:
        from PIL import Image
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return base64.b64encode(buf.getvalue()).decode("utf-8"), "image/jpeg"
    except Exception as exc:
        log.warning("Vertex video: image conversion failed (%s): %s", mime_type, exc)
        return b64, mime


def _get_vertex_token() -> str:
    """Get Bearer token for Vertex AI (blocking, run in thread). Uses shared cache."""
    from ..vertex_auth import get_vertex_token
    return get_vertex_token()


async def submit_vertex_video(
    request: VideoGenRequest,
    project: str,
    region: str,
    model: str,
) -> VideoSubmitResult:
    """
    Submit a Veo video generation job via Vertex AI.
    Returns operation name (full path) for polling.
    """
    effective_prompt = request.prompt.strip()
    if request.brand_memory and request.brand_memory.strip():
        effective_prompt = (
            f"{effective_prompt}\n\n"
            f"Brand context: {request.brand_memory.strip()}\n"
            "Apply the brand identity to the visual style, lighting, and scene composition."
        )

    instance: dict = {"prompt": effective_prompt}
    if request.image_base64:
        img_b64, img_mime = _ensure_veo_compatible(
            request.image_base64, request.image_mime_type
        )
        instance["image"] = {
            "bytesBase64Encoded": img_b64,
            "mimeType": img_mime,
        }
    if request.end_image_base64:
        end_b64, end_mime = _ensure_veo_compatible(
            request.end_image_base64, request.end_image_mime_type
        )
        instance["lastFrame"] = {
            "bytesBase64Encoded": end_b64,
            "mimeType": end_mime,
        }

    # Veo 3: durationSeconds 4, 6, or 8
    duration = getattr(request, "duration_seconds", 8)
    if duration not in (4, 6, 8):
        duration = 8 if duration >= 8 else (6 if duration >= 6 else 4)

    # FIX: 1080p and 4k require durationSeconds=8 — enforce it silently
    resolution = getattr(request, "resolution", "720p") or "720p"
    if resolution in _HIGH_RES_REQUIRES_8S and duration != 8:
        log.warning(
            "Vertex video: resolution=%s requires duration_seconds=8, overriding from %ds to 8s",
            resolution, duration,
        )
        duration = 8

    payload = {
        "instances": [instance],
        "parameters": {
            "aspectRatio": request.aspect_ratio,
            "sampleCount": request.sample_count,
            "durationSeconds": duration,
            "resolution": resolution,  # FIX: was missing — caused silent 720p fallback
        },
    }

    url = (
        f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}/"
        f"locations/{region}/publishers/google/models/{model}:predictLongRunning"
    )

    token = await asyncio.to_thread(_get_vertex_token)
    client = get_default_client()
    resp = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code >= 400:
        try:
            body = resp.json()
            msg = body.get("error", {}).get("message", f"HTTP {resp.status_code}")
        except Exception:
            msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
        from ..creative_studio.exceptions import GeminiError
        raise GeminiError(f"Vertex Veo API error: {msg}")

    data = resp.json()
    operation_name = data.get("name")
    if not operation_name:
        from ..creative_studio.exceptions import GeminiError
        raise GeminiError(
            "Vertex Veo accepted the request but did not return an operation name. "
            f"Response keys: {list(data.keys())}"
        )

    log.info("Vertex video: submit accepted operation=%s model=%s", operation_name, model)
    return VideoSubmitResult(
        operation_id=operation_name,
        provider_used="vertex",
        model_used=model,
    )


async def poll_vertex_video(operation_name: str) -> VideoPollResult:
    """
    Poll a Vertex AI long-running video operation.
    Operation name format: projects/{project}/locations/{region}/publishers/google/models/{model}/operations/{id}

    Publisher model operations must use fetchPredictOperation (POST), not the standard operations GET.
    """
    # resource_name = everything before /operations/ (e.g. projects/.../models/veo-3.1-generate-preview)
    resource_name = operation_name.rpartition("/operations/")[0]
    if not resource_name:
        from ..creative_studio.exceptions import GeminiError
        raise GeminiError("Invalid Vertex operation name: missing /operations/")

    parts = operation_name.split("/")
    region = "us-central1"
    for i, p in enumerate(parts):
        if p == "locations" and i + 1 < len(parts):
            region = parts[i + 1]
            break

    poll_url = f"https://{region}-aiplatform.googleapis.com/v1/{resource_name}:fetchPredictOperation"
    payload = {"operationName": operation_name}
    token = await asyncio.to_thread(_get_vertex_token)

    client = get_default_client()
    resp = await client.post(
        poll_url,
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    if resp.status_code >= 400:
        try:
            body = resp.json()
            msg = body.get("error", {}).get("message", f"HTTP {resp.status_code}")
        except Exception:
            msg = f"HTTP {resp.status_code}"
        from ..creative_studio.exceptions import GeminiError
        raise GeminiError(f"Vertex poll API error: {msg}")

    data = resp.json()

    if not data.get("done", False):
        return VideoPollResult(done=False)

    if "error" in data:
        error_msg = data["error"].get("message", "Vertex Veo operation failed.")
        log.warning("Vertex video poll: operation=%s failed: %s", operation_name, error_msg)
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
                "Operation completed but no video data was found. "
                f"Response keys: {list(response.keys())}"
            ),
        )

    async def _resolve_video(vid: dict, index: int) -> VideoItem:
        if "video" in vid and isinstance(vid["video"], dict):
            vid = vid["video"]
        b64 = vid.get("bytesBase64Encoded")
        mime = vid.get("mimeType", "video/mp4")
        uri = vid.get("uri", "")
        if not b64 and uri:
            token = await asyncio.to_thread(_get_vertex_token)
            long_client = get_long_client()
            video_resp = await long_client.get(
                uri,
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=True,
            )
            if video_resp.status_code != 200:
                from ..creative_studio.exceptions import GeminiError
                raise GeminiError(f"Video[{index}] URI fetch failed: HTTP {video_resp.status_code}")
            b64 = base64.b64encode(video_resp.content).decode("utf-8")
            mime = video_resp.headers.get("content-type", mime).split(";")[0].strip()
        if not b64:
            from ..creative_studio.exceptions import GeminiError
            raise GeminiError(f"Video[{index}] completed but no data or URI found.")
        return VideoItem(data_url=f"data:{mime};base64,{b64}", mime_type=mime)

    resolved = await asyncio.gather(*[_resolve_video(v, i) for i, v in enumerate(videos)])
    log.info("Vertex video: poll complete operation=%s videos=%d", operation_name, len(resolved))
    return VideoPollResult(
        done=True,
        videos=list(resolved),
        data_url=resolved[0].data_url,
        mime_type=resolved[0].mime_type,
    )