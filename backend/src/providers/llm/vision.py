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
from ...core.exceptions import ProviderUnavailable, VisionPayloadTooLarge
from ...core.middleware.logging import get_logger
from ._openai_compatible import call_openai_compatible_chat
from .base import LLMResult
from .key_cooldown import is_cooling_down, mark_rate_limited

log = get_logger(__name__)

# Real, explicit safety margin (2026-09-25, per an explicit user ask: "compress a little just to
# be on the safe side") — Groq's vision endpoint has no documented hard pixel limit the way
# Cloudflare's FLUX.2 [klein] does (`providers/image/cloudflare_flux.py`'s own 511px constant is a
# real, DOCUMENTED vendor constraint; this one is a deliberate margin, not a hard limit this
# provider enforces). A fixed pixel dimension cap doesn't actually bound payload size — a
# highly-detailed 1024px image can still be large, while a simple one could be shrunk further
# without losing anything useful — so the real budget this function enforces is a BYTE size, with
# dimensions/quality as the knobs it turns to hit that budget.
_MAX_VISION_BYTES = 5 * 1024 * 1024  # 5MB — a generous margin under typical provider base64 payload limits
_COMPRESSION_STEPS: list[tuple[int, float]] = [
    # (JPEG quality, longest-edge scale factor relative to the ORIGINAL image), tried in order
    # until the result fits under _MAX_VISION_BYTES. First step is usually sufficient for a normal
    # canvas asset; later steps exist for the rare oversized/highly-detailed image.
    (85, 1.0),
    (70, 0.75),
    (55, 0.5),
    (40, 0.35),
    (30, 0.25),
]


def _compress_for_vision(image_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Shrinks a real canvas asset under `_MAX_VISION_BYTES` before sending it to the vision model.
    Returns (bytes, mime_type), always re-encoded as JPEG. Unlike the dimension-only cap this
    replaces, a decode failure is now a hard failure (re-raised), not a silent fallback to the
    original, unprocessed bytes — matches the fail-closed intent already used elsewhere in the
    compliance path rather than quietly sending a provider a payload this function couldn't even
    open. If every compression step still leaves the image over budget, raises
    `VisionPayloadTooLarge` instead of sending an oversized payload."""
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    original_size = img.size
    last_bytes = b""
    for quality, scale in _COMPRESSION_STEPS:
        candidate = img
        if scale < 1.0:
            target = (max(1, int(original_size[0] * scale)), max(1, int(original_size[1] * scale)))
            candidate = img.copy()
            candidate.thumbnail(target, Image.LANCZOS)
        buf = io.BytesIO()
        candidate.save(buf, format="JPEG", quality=quality)
        last_bytes = buf.getvalue()
        if len(last_bytes) <= _MAX_VISION_BYTES:
            return last_bytes, "image/jpeg"
    raise VisionPayloadTooLarge(len(last_bytes), _MAX_VISION_BYTES)


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
    size for a call that only needs to SEE the image" behavior `_compress_for_vision` exists for,
    done by Cloudinary's CDN instead of this backend's CPU. Falls back to the original
    download-resize-base64 path when no direct url exists (local-disk dev mode) — `image_bytes`
    is required in that case."""
    if image_url:
        # Only apply Cloudinary's transform syntax to an actual Cloudinary IMAGE delivery url —
        # never touch a video/raw url or a non-Cloudinary host, which wouldn't understand it.
        transformed = image_url.replace("/image/upload/", "/image/upload/w_1024,q_85,f_jpg/", 1)
        return {"type": "image_url", "image_url": {"url": transformed}}
    assert image_bytes is not None, "image_bytes is required when no direct image_url is available"
    resized_bytes, resized_mime = _compress_for_vision(image_bytes, mime_type)
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
    # Same cooldown-ordering hint as groq.py — try keys not currently cooling down from a recent
    # 429 first, still falling through to a cooling-down key if every key is.
    ordered_keys = sorted(keys, key=is_cooling_down)
    last_error: ProviderUnavailable | None = None
    for key in ordered_keys:
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
            if "rate limited" in exc.message:
                mark_rate_limited(key)
            continue

    if last_error is not None:
        log.warning("vision_falling_back_to_gemini", extra={"_extra_groq_error": last_error.message})
    else:
        log.warning("vision_groq_key_missing_falling_back_to_gemini")
    emit("llm_provider_fallback", tier="vision", from_provider="groq", to_provider="gemini")

    if not settings.gemini_api_key:
        if last_error is not None:
            raise last_error
        else:
            raise ProviderUnavailable("groq", "No Groq API keys available and no Gemini fallback configured.")

    return await call_openai_compatible_chat(
        provider_name="gemini",
        base_url=settings.gemini_base_url,
        api_key=settings.gemini_api_key,
        model=settings.gemini_vision_model,
        messages=[{"role": "system", "content": system}, user_message],
        tools=None,
        max_tokens=max_tokens,
    )
