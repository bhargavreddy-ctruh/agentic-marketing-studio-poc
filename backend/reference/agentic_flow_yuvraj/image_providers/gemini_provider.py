"""
gemini_provider.py -- Gemini API image generation provider.

Uses REST API (generativelanguage.googleapis.com) with API key rotation.
Supports text-to-image and image-to-image (multimodal generateContent).
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Awaitable, Callable, Union

import httpx

from ..http_client import get_medium_client
from .types import ImageGenRequest, ImageResult

log = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
_TIMEOUT = 180.0

KeyGetter = Callable[[], Union[str, Awaitable[str]]]
ReleaseKey = Callable[[str], None]


def parse_retry_after_sec(resp: httpx.Response) -> float | None:
    """Extract retry delay from Retry-After header or Google error RetryInfo."""
    retry_after = getattr(resp, "headers", {}).get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return None

    error = data.get("error", {})
    for detail in error.get("details", []):
        if "RetryInfo" in str(detail.get("@type", "")):
            delay = detail.get("retryDelay", "")
            if isinstance(delay, str) and delay.endswith("s"):
                try:
                    return float(delay[:-1])
                except ValueError:
                    pass
    return None


async def _resolve_key(get_key: KeyGetter) -> str:
    result = get_key()
    if inspect.isawaitable(result):
        return await result
    return result


async def generate_gemini(
    request: ImageGenRequest,
    models: list[str],
    get_key: KeyGetter,
    report_success: Callable[[str], None],
    report_failure: Callable[..., None],
    pacer: Callable[[str], Awaitable[None]] | None = None,
    release_key: ReleaseKey | None = None,
    max_key_attempts: int = 3,
) -> ImageResult | None:
    """
    Generate image via Gemini API. Tries models in order; on 429/503 rotates
    to the next available key (via ``get_key`` / ``acquire``) before trying
    the next model.
    """
    if not request.prompt or not request.prompt.strip():
        return None

    parts = []
    if request.source_images:
        for img in request.source_images:
            parts.append({
                "inlineData": {
                    "mimeType": img.mime_type,
                    "data": img.data_b64,
                }
            })
    user_text = request.prompt.strip()
    parts.append({"text": user_text})

    payload: dict = {
        "contents": [{"parts": parts}],
    }
    if request.system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": request.system_instruction}]
        }
    gen_config = payload.setdefault("generationConfig", {})
    gen_config["responseModalities"] = ["TEXT", "IMAGE"]
    if request.aspect_ratio:
        gen_config.setdefault("imageConfig", {})["aspectRatio"] = request.aspect_ratio
    if request.source_images:
        temp = (request.metadata or {}).get("temperature")
        gen_config["temperature"] = temp if temp is not None else 1.0

    last_exc: Exception | None = None
    client = get_medium_client()

    for model in models:
        url = f"{GEMINI_BASE_URL}/v1beta/models/{model}:generateContent"

        for _ in range(max(1, max_key_attempts)):
            key = await _resolve_key(get_key)
            try:
                if pacer is not None:
                    await pacer(key)

                headers = {
                    "Content-Type": "application/json",
                    "x-goog-api-key": key,
                }
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    log.error(
                        "Gemini provider: connection failure to %s: %s",
                        url, exc,
                    )
                    raise exc
                except httpx.TimeoutException as exc:
                    log.warning("Gemini provider: %s timeout: %s", model, exc)
                    report_failure(key, 429)
                    last_exc = exc
                    continue
                except httpx.RequestError as exc:
                    log.warning("Gemini provider: %s network error: %s", model, exc)
                    last_exc = exc
                    break

                if resp.status_code in (429, 503):
                    retry_after = (
                        parse_retry_after_sec(resp)
                        if resp.status_code == 429
                        else None
                    )
                    report_failure(key, resp.status_code, retry_after)
                    last_exc = Exception(f"HTTP {resp.status_code}")
                    continue

                if resp.status_code >= 500:
                    # Transient upstream error (500/502/504). Rotate to another
                    # key and retry; the key is healthy, so it is not cooled.
                    log.warning("Gemini provider: %s HTTP %d, retrying on next key", model, resp.status_code)
                    last_exc = Exception(f"HTTP {resp.status_code}")
                    continue

                if resp.status_code >= 400:
                    log.warning("Gemini provider: %s HTTP %d", model, resp.status_code)
                    last_exc = Exception(f"HTTP {resp.status_code}")
                    break

                try:
                    data = resp.json()
                except Exception as exc:
                    log.warning("Gemini provider: %s invalid JSON: %s", model, exc)
                    break

                error_msg = str(data.get("error", {}).get("message", ""))
                if "timed out" in error_msg.lower() or "generation timed out" in error_msg.lower():
                    log.warning("Gemini provider: %s server-side timeout: %s", model, error_msg)
                    report_failure(key, 429)
                    last_exc = Exception(f"Gemini generation timed out: {error_msg}")
                    continue

                candidates = data.get("candidates", [])
                if not candidates:
                    log.warning("Gemini provider: %s no candidates", model)
                    break

                response_parts = candidates[0].get("content", {}).get("parts", [])
                image_part = next(
                    (
                        p for p in response_parts
                        if p.get("inlineData", {}).get("mimeType", "").startswith("image/")
                    ),
                    None,
                )
                if image_part is None:
                    finish_reason = candidates[0].get("finishReason", "")
                    log.warning(
                        "Gemini provider: %s no image (finishReason=%s)",
                        model, finish_reason,
                    )
                    if finish_reason == "RECITATION":
                        break
                    break

                b64 = image_part["inlineData"]["data"]
                mime_type = image_part["inlineData"]["mimeType"]
                report_success(key)
                log.debug("Gemini provider: success model=%s", model)
                return ImageResult(
                    image_base64=b64,
                    mime_type=mime_type,
                    data_url=f"data:{mime_type};base64,{b64}",
                    provider_used="gemini",
                    model_used=model,
                )
            finally:
                if release_key is not None:
                    release_key(key)

    if last_exc:
        raise last_exc
    return None
