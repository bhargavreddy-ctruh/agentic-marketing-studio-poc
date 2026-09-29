"""
THE ONLY file that calls a vision-capable model directly, bypassing the Tier 1/2/3 system
entirely — vision is a genuinely different CAPABILITY question ("can this model see an image at
all"), not a "how smart does this need to be" tier choice, and none of the pinned Tier models
support image input.

Uses Groq's `qwen/qwen3.8-27b` — confirmed live and free (Memory.md, Phase 4): given a real
generated image, it correctly identified the actual object, setting, and even spotted a
"pollinations.ai" watermark, using the exact same Groq account/key already configured for this
project's primary LLM routing. No new signup, no new provider account.

Fallback (2026-09-25, per an explicit user ask: apply the same "try Groq once per key, then fall
back" policy leads/specialists already get via `router.py` to vision calls too — this file used
to be the one LLM call path in the whole codebase with zero fallback). `router.py` itself isn't
reused here because its `LLMProvider.complete()` contract has no vision-specific shape; instead
this mirrors its exact policy directly: one Groq attempt (already zero-retry-per-key internally,
see `groq.py`), and on `ProviderUnavailable` fall back to `ReplicateLLMProvider` — confirmed live
against Replicate's own schema (api.replicate.com/v1/models/google/gemini-2.5-flash) that
`google/gemini-2.5-flash` genuinely accepts an `images` field, so this is a REAL vision fallback,
not a text-only guess (`replicate_llm.py`'s own docstring has the schema detail).
"""
from __future__ import annotations

import base64
import io

from PIL import Image

from ...core.config import settings
from ...core.events import emit
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from ._openai_compatible import call_openai_compatible_chat
from .base import LLMResult, ModelTier
from .replicate_llm import get_replicate_llm_provider

log = get_logger(__name__)

# Real, explicit safety margin (2026-09-25, per an explicit user ask: "compress a little just to
# be on the safe side") — Groq's vision endpoint has no documented hard pixel limit the way
# Cloudflare's FLUX.2 [klein] does (`providers/image/cloudflare_flux.py`'s own 511px constant is a
# real, DOCUMENTED vendor constraint; this one is a deliberate margin, not a hard limit this
# provider enforces), but a full-size canvas asset base64-encoded raw is real, avoidable payload
# weight and latency for a call that only needs to SEE the image, not reproduce it pixel-for-pixel.
# 1024px keeps genuinely useful visual detail (colors, composition, mood, legible watermarks —
# exactly what this function is used for) while capping payload size. Re-encoded as JPEG
# (quality=85) rather than PNG — photographic canvas assets compress far smaller as JPEG with no
# visible quality loss at this use case's resolution, directly cutting the base64 payload further.
_MAX_VISION_DIMENSION = 1024


def _downscale_for_vision(image_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Shrinks a real canvas asset before sending it to the vision model — a deliberate safety
    margin, not a vendor-mandated limit (contrast `cloudflare_flux.py`'s real 511px constraint).
    Returns (bytes, mime_type): on success, always re-encoded as JPEG for the payload-size win
    (images already small enough pass through PIL's `.thumbnail()` unchanged in dimensions, but
    are still re-encoded). On any decode failure, returns the ORIGINAL bytes and ORIGINAL
    mime_type untouched — a real, disclosed degrade (best-effort compression, never a hard
    requirement to see the image at all) rather than failing the whole vision call over a resize
    step, and never mislabeling un-re-encoded bytes with the wrong mime type."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((_MAX_VISION_DIMENSION, _MAX_VISION_DIMENSION), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return image_bytes, mime_type


def _build_image_url_content(
    *, image_bytes: bytes | None, mime_type: str, image_url: str | None
) -> dict:
    """Real, live-found latency win (2026-09-29, per an explicit user ask to reduce latency by
    passing URL-based images to the LLM wherever possible): when the asset already has a real
    Cloudinary CDN url, hand the model THAT directly — both Groq's `image_url.url` and
    `replicate_llm.py`'s `images` field genuinely accept a real https:// URI, not just a `data:`
    one (confirmed in that file's own docstring: "array of URIs"). This skips downloading the
    asset a second time, the PIL resize, AND the base64 33%-size inflation entirely — Cloudinary's
    own on-the-fly transform suffix (`w_1024,q_85,f_jpg`) reproduces the exact same "cap payload
    size for a call that only needs to SEE the image" behavior `_downscale_for_vision` exists for,
    done by Cloudinary's CDN instead of this backend's CPU. Falls back to the original
    download-resize-base64 path when no direct url exists (local-disk dev mode) — `image_bytes`
    is required in that case."""
    if image_url:
        # Only apply Cloudinary's transform syntax to an actual Cloudinary IMAGE delivery url —
        # never touch a video/raw url or a non-Cloudinary host, which wouldn't understand it.
        transformed = image_url.replace("/image/upload/", "/image/upload/w_1024,q_85,f_jpg/", 1)
        return {"type": "image_url", "image_url": {"url": transformed}}
    assert image_bytes is not None, "image_bytes is required when no direct image_url is available"
    resized_bytes, resized_mime = _downscale_for_vision(image_bytes, mime_type)
    b64 = base64.b64encode(resized_bytes).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{resized_mime};base64,{b64}"}}


async def complete_with_vision(
    *,
    image_bytes: bytes | None = None,
    mime_type: str = "image/jpeg",
    image_url: str | None = None,
    system: str,
    question: str,
    max_tokens: int = 700,
) -> LLMResult:
    user_message = {
        "role": "user",
        "content": [
            {"type": "text", "text": question},
            _build_image_url_content(image_bytes=image_bytes, mime_type=mime_type, image_url=image_url),
        ],
    }

    # Same per-key policy as groq.py: GROQ_API_KEY may be a comma-separated list; try each key
    # once (retries=0), fall through to the next on a rate limit, and only fall back to Replicate
    # once every key has failed.
    keys = [k.strip() for k in (settings.groq_api_key or "").split(",") if k.strip()]
    last_error: ProviderUnavailable | None = None
    for key in keys:
        try:
            return await call_openai_compatible_chat(
                provider_name="groq",
                base_url=settings.groq_base_url,
                api_key=key,
                model=settings.groq_vision_model,
                messages=[{"role": "system", "content": system}, user_message],
                tools=None,
                max_tokens=max_tokens,
                retries=0,  # same "try once per key, then fall back" policy as router.py/groq.py
            )
        except ProviderUnavailable as exc:
            last_error = exc
            continue

    if last_error is not None:
        log.warning("vision_falling_back_to_replicate", extra={"_extra_groq_error": last_error.message})
    else:
        log.warning("vision_groq_key_missing_falling_back_to_replicate")
    emit("llm_provider_fallback", tier="vision", from_provider="groq", to_provider="replicate_llm")

    return await get_replicate_llm_provider().complete(
        tier=ModelTier.TIER_3,
        system=system,
        messages=[user_message],
        tools=None,
        max_tokens=max_tokens,
    )
