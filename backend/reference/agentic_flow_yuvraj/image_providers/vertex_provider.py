"""
vertex_provider.py -- Vertex AI image generation provider.

Uses google-genai SDK with vertexai=True.
Supports text-to-image and image-to-image (multimodal generate_content).
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

from .types import ImageGenRequest, ImageResult

log = logging.getLogger(__name__)


def _get_mime_type(image_bytes: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if len(image_bytes) >= 8 and image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


async def generate_vertex(
    request: ImageGenRequest,
    models: list[str],
    project: str,
    region: str,
) -> ImageResult | None:
    """
    Generate image via Vertex AI. Tries models in order.

    Args:
        request: Image generation request.
        models: Model fallback chain.
        project: Google Cloud project ID.
        region: Google Cloud region (e.g. us-central1).

    Returns:
        ImageResult on success, None if all attempts fail.
    """
    if not project or not request.prompt or not request.prompt.strip():
        return None

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log.warning("Vertex provider: google-genai not installed")
        return None

    try:
        client = genai.Client(
            vertexai=True,
            project=project,
            location=region,
            http_options=types.HttpOptions(timeout=120_000),
        )
    except Exception as exc:
        log.warning("Vertex provider: failed to create client: %s", exc)
        return None

    parts: list = []
    if request.source_images:
        for img in request.source_images:
            try:
                data = base64.b64decode(img.data_b64)
            except Exception:
                continue
            parts.append(
                types.Part(
                    inline_data=types.Blob(
                        data=data,
                        mime_type=img.mime_type or _get_mime_type(data),
                    )
                )
            )
    parts.append(types.Part(text=request.prompt.strip()))

    gen_config_args = {}
    if request.system_instruction:
        gen_config_args["system_instruction"] = request.system_instruction
    if request.aspect_ratio:
        gen_config_args["image_config"] = types.ImageConfig(aspect_ratio=request.aspect_ratio)
    gen_config_args["response_modalities"] = ["TEXT", "IMAGE"]
    if request.source_images:
        gen_config_args["temperature"] = 1.0

    config = types.GenerateContentConfig(**gen_config_args)

    last_exc: Optional[Exception] = None
    for model in models:
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=types.Content(parts=parts, role="user"),
                config=config,
            )
        except Exception as exc:
            log.warning("Vertex provider: %s error: %s", model, exc)
            last_exc = exc
            continue

        if not response.candidates or not response.candidates[0].content.parts:
            log.warning("Vertex provider: %s no candidates/parts", model)
            continue

        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                encoded = base64.b64encode(part.inline_data.data).decode("utf-8")
                mime = getattr(part.inline_data, "mime_type", None) or "image/png"
                log.info("Vertex provider: success model=%s", model)
                return ImageResult(
                    image_base64=encoded,
                    mime_type=mime,
                    data_url=f"data:{mime};base64,{encoded}",
                    provider_used="vertex",
                    model_used=model,
                )

        log.warning("Vertex provider: %s no image part in response", model)
        continue

    if last_exc:
        raise last_exc
    return None
