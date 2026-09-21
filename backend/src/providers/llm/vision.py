"""
THE ONLY file that calls a vision-capable model directly, bypassing the Tier 1/2/3 system
entirely — vision is a genuinely different CAPABILITY question ("can this model see an image at
all"), not a "how smart does this need to be" tier choice, and none of the pinned Tier models
support image input.

Uses Groq's `qwen/qwen3.8-27b` — confirmed live and free (Memory.md, Phase 4): given a real
generated image, it correctly identified the actual object, setting, and even spotted a
"pollinations.ai" watermark, using the exact same Groq account/key already configured for this
project's primary LLM routing. No new signup, no new provider account.
"""
from __future__ import annotations

import base64

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ._openai_compatible import call_openai_compatible_chat
from .base import LLMResult


async def complete_with_vision(
    *, image_bytes: bytes, mime_type: str, system: str, question: str, max_tokens: int = 700
) -> LLMResult:
    if not settings.groq_api_key:
        raise ProviderUnavailable("groq", "GROQ_API_KEY is not set (required for vision checks)")

    b64 = base64.b64encode(image_bytes).decode()
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
            ],
        },
    ]
    return await call_openai_compatible_chat(
        provider_name="groq",
        base_url=settings.groq_base_url,
        api_key=settings.groq_api_key,
        model=settings.groq_vision_model,
        messages=messages,
        tools=None,
        max_tokens=max_tokens,
    )
