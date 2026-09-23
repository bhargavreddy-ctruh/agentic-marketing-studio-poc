"""
bedrock_provider.py -- Amazon Bedrock image generation provider.

Uses Bedrock Runtime REST API with Bearer token, or boto3 when
AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY are set.
Supports TEXT_IMAGE, IMAGE_VARIATION, and VIRTUAL_TRY_ON (Nova Canvas).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import random
import os
from urllib.parse import quote

import httpx

from ..http_client import get_medium_client
from .types import ImageGenRequest, ImageResult

log = logging.getLogger(__name__)

# Nova only supports JPEG and PNG; convert WebP, GIF, etc. to JPEG
_NOVA_SUPPORTED_MIME = frozenset({"image/jpeg", "image/jpg", "image/png"})


def _ensure_nova_compatible(b64: str, mime_type: str) -> str:
    """Convert image to JPEG if format not supported by Nova (e.g. WebP)."""
    mime = (mime_type or "").lower().strip()
    if mime in _NOVA_SUPPORTED_MIME:
        return b64
    try:
        from PIL import Image
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as exc:
        log.warning("Image conversion failed (%s): %s", mime_type, exc)
        return b64

_TIMEOUT = 180.0

# Titan vs Nova dimension mapping
_TITAN_ASPECT = {
    "1:1": (1024, 1024), "4:3": (1152, 896), "3:4": (896, 1152),
    "16:9": (1344, 768), "9:16": (768, 1344), "2:3": (768, 1152), "3:2": (1152, 768),
}
_NOVA_ASPECT = {
    "1:1": (512, 512), "4:3": (576, 448), "3:4": (448, 576),
    "16:9": (672, 384), "9:16": (384, 672), "2:3": (384, 576), "3:2": (576, 384),
}


def _get_dims(aspect_ratio: str, model_id: str) -> tuple[int, int]:
    if model_id and "nova-canvas" in model_id:
        return _NOVA_ASPECT.get(aspect_ratio, (512, 512))
    return _TITAN_ASPECT.get(aspect_ratio, (1024, 1024))


def _get_seed(model_id: str | None) -> int:
    if model_id and "nova-canvas" in str(model_id):
        return random.randint(0, 858993459)
    return random.randint(0, 2147483647)


def _has_access_keys(access_key: str, secret_key: str) -> bool:
    return bool(access_key and secret_key)


# Nova Canvas model IDs that support VIRTUAL_TRY_ON
_NOVA_VTO_MODELS = ("amazon.nova-canvas-v1:0", "amazon.nova-canvas-v1:1")


async def _generate_nova_virtual_tryon(
    request: ImageGenRequest,
    models: list[str],
    region: str,
    api_key: str,
    access_key: str = "",
    secret_key: str = "",
) -> ImageResult | None:
    """
    Nova Canvas virtual try-on: sourceImage=person, referenceImage=garment.
    Only Nova Canvas models support VIRTUAL_TRY_ON.
    """
    use_boto3 = _has_access_keys(access_key, secret_key)
    if not use_boto3:
        api_key = api_key or os.getenv("AWS_BEARER_TOKEN_BEDROCK", "")
        if not api_key:
            log.warning("Bedrock provider: API_KEY or credentials not configured for Nova VTO")
            return None

    # Filter to Nova Canvas models only (Titan does not support VTO)
    vto_models = [m for m in models if m and "nova-canvas" in m]
    if not vto_models:
        vto_models = list(_NOVA_VTO_MODELS)

    garment_class = "UPPER_BODY"
    if request.metadata and "garment_class" in request.metadata:
        gc = str(request.metadata["garment_class"]).upper()
        if gc in ("UPPER_BODY", "LOWER_BODY", "FOOTWEAR", "FULL_BODY"):
            garment_class = gc

    # Nova supports JPEG/PNG only; convert WebP and other formats to JPEG
    person_b64 = _ensure_nova_compatible(
        request.source_images[0].data_b64,
        request.source_images[0].mime_type or "image/jpeg",
    )
    garment_b64 = _ensure_nova_compatible(
        request.source_images[1].data_b64,
        request.source_images[1].mime_type or "image/jpeg",
    )

    body = {
        "taskType": "VIRTUAL_TRY_ON",
        "virtualTryOnParams": {
            "sourceImage": person_b64,
            "referenceImage": garment_b64,
            "maskType": "GARMENT",
            "garmentBasedMask": {"garmentClass": garment_class},
        },
        "imageGenerationConfig": {
            "numberOfImages": 1,
            "quality": "standard",
        },
    }

    last_err: Exception | None = None
    for mid in vto_models:
        try:
            if use_boto3:
                def _invoke() -> dict:
                    import boto3
                    client = boto3.client(
                        "bedrock-runtime",
                        region_name=region,
                        aws_access_key_id=access_key,
                        aws_secret_access_key=secret_key,
                    )
                    resp = client.invoke_model(
                        modelId=mid,
                        body=json.dumps(body),
                        contentType="application/json",
                        accept="application/json",
                    )
                    return json.loads(resp["body"].read().decode())

                data = await asyncio.to_thread(_invoke)
            else:
                url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{quote(mid, safe='')}/invoke"
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
                client = get_medium_client()
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code >= 400:
                    last_err = RuntimeError(f"Bedrock HTTP {resp.status_code}")
                    continue
                data = resp.json()

            images = data.get("images", [])
            if not images:
                err = data.get("error", "")
                last_err = RuntimeError(err or "No image in response")
                continue

            b64 = images[0]
            log.info("Bedrock Nova VTO: success model=%s garment_class=%s", mid, garment_class)
            return ImageResult(
                image_base64=b64,
                mime_type="image/png",
                data_url=f"data:image/png;base64,{b64}",
                provider_used="aws",
                model_used=mid,
            )
        except Exception as exc:
            last_err = exc
            log.warning("Bedrock Nova VTO %s failed: %s", mid, exc)
            continue

    if last_err:
        raise last_err
    return None


async def generate_bedrock(
    request: ImageGenRequest,
    models: list[str],
    region: str,
    api_key: str,
    *,
    access_key: str = "",
    secret_key: str = "",
) -> ImageResult | None:
    """
    Generate image via Bedrock. Supports virtual try-on (2 images) with Nova Canvas.

    Args:
        request: Image generation request.
        models: Model fallback chain (e.g. Titan, Nova Canvas).
        region: AWS region.
        api_key: Bedrock Bearer API key (used when access_key/secret_key not set).
        access_key: AWS_ACCESS_KEY_ID (optional, uses boto3 when set with secret_key).
        secret_key: AWS_SECRET_ACCESS_KEY (optional).

    Returns:
        ImageResult on success, None if unsupported or all attempts fail.
    """
    region = region or os.getenv("AWS_REGION", "us-east-1")

    # Room compose (room + furniture): Bedrock has no API for this; skip to avoid Nova VTO misuse
    if request.metadata and request.metadata.get("task") == "room_compose":
        log.debug("Bedrock provider: skipping (room_compose not supported; use Gemini/Vertex)")
        return None

    # Virtual try-on: 2 images (person + garment) — Nova Canvas only
    if request.source_images and len(request.source_images) == 2:
        return await _generate_nova_virtual_tryon(
            request=request,
            models=models,
            region=region,
            api_key=api_key,
            access_key=access_key,
            secret_key=secret_key,
        )

    if not request.prompt or not request.prompt.strip():
        return None

    # Titan/Nova: skip if 2+ images for non-VTO (only IMAGE_VARIATION supports 1 image)
    if request.source_images and len(request.source_images) > 1:
        log.debug("Bedrock provider: skipping (multi-image only supported for Nova VTO)")
        return None

    use_boto3 = _has_access_keys(access_key, secret_key)
    if not use_boto3:
        api_key = api_key or os.getenv("AWS_BEARER_TOKEN_BEDROCK", "")
        if not api_key:
            log.warning("Bedrock provider: API_KEY or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY not configured")
            return None

    models = [m for m in models if m] or ["amazon.titan-image-generator-v2:0"]
    prompt = request.prompt.strip()[:512]
    neg = (request.negative_text or "").strip()[:512] or None
    aspect = request.aspect_ratio or "1:1"

    last_err: Exception | None = None
    for mid in models:
        w, h = _get_dims(aspect, mid)
        if request.source_images and len(request.source_images) == 1:
            # IMAGE_VARIATION
            params = {
                "images": [request.source_images[0].data_b64],
                "similarityStrength": 0.7,
            }
            if prompt:
                params["text"] = prompt
            if neg:
                params["negativeText"] = neg
            body = {
                "taskType": "IMAGE_VARIATION",
                "imageVariationParams": params,
                "imageGenerationConfig": {
                    "numberOfImages": 1,
                    "quality": "standard",
                    "cfgScale": 8.0,
                    "height": h,
                    "width": w,
                },
            }
        else:
            # TEXT_IMAGE
            text_params = {"text": prompt}
            if neg:
                text_params["negativeText"] = neg
            body = {
                "taskType": "TEXT_IMAGE",
                "textToImageParams": text_params,
                "imageGenerationConfig": {
                    "numberOfImages": 1,
                    "quality": "standard",
                    "cfgScale": 8.0,
                    "height": h,
                    "width": w,
                    "seed": _get_seed(mid),
                },
            }

        if use_boto3:
            def _invoke() -> dict:
                import boto3
                client = boto3.client(
                    "bedrock-runtime",
                    region_name=region,
                    aws_access_key_id=access_key,
                    aws_secret_access_key=secret_key,
                )
                resp = client.invoke_model(
                    modelId=mid,
                    body=json.dumps(body),
                    contentType="application/json",
                    accept="application/json",
                )
                return json.loads(resp["body"].read().decode())

            try:
                data = await asyncio.to_thread(_invoke)
            except Exception as exc:
                log.warning("Bedrock provider (boto3): %s %s", mid, exc)
                last_err = exc if isinstance(exc, RuntimeError) else RuntimeError(str(exc))
                if len(models) > 1:
                    continue
                raise last_err
        else:
            url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{quote(mid, safe='')}/invoke"
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

            try:
                client = get_medium_client()
                resp = await client.post(url, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                log.warning("Bedrock provider: %s timeout: %s", mid, exc)
                last_err = exc
                continue
            except httpx.RequestError as exc:
                log.warning("Bedrock provider: %s network error: %s", mid, exc)
                last_err = exc
                continue

            if resp.status_code == 429:
                log.warning("Bedrock provider: %s throttled", mid)
                continue
            if resp.status_code >= 400:
                log.warning("Bedrock provider: %s HTTP %d", mid, resp.status_code)
                last_err = RuntimeError(f"Bedrock HTTP {resp.status_code}")
                if resp.status_code == 400 and len(models) > 1:
                    continue
                raise last_err

            try:
                data = resp.json()
            except json.JSONDecodeError as exc:
                log.warning("Bedrock provider: %s invalid JSON: %s", mid, exc)
                continue

        images = data.get("images", [])
        err = data.get("error", "")
        if not images and err:
            log.warning("Bedrock provider: %s content filter: %s", mid, err[:80])
            if len(models) > 1:
                continue
            last_err = RuntimeError(f"Bedrock: {err}")
            continue

        if not images:
            log.warning("Bedrock provider: %s no image in response", mid)
            continue

        b64 = images[0]
        log.info("Bedrock provider: success model=%s", mid)
        return ImageResult(
            image_base64=b64,
            mime_type="image/png",
            data_url=f"data:image/png;base64,{b64}",
            provider_used="aws",
            model_used=mid,
        )

    if last_err:
        raise last_err
    return None
