"""
THE ONLY file that would talk to Gemini's image model — registered but INACTIVE by default.

Stub only, on purpose: the existing agentic_flow codebase's image_providers/gemini_provider.py +
gemini_key_rotator.py already contain a full, proven implementation of this (fallback chain,
health-aware key rotation). That code is NOT ported here yet because this provider is inactive
(Gemini/Veo free-tier quota on this project's account is exhausted — Architecture.md section 3).

When quota is available again: port image_providers/gemini_provider.py's logic into this file,
flip settings.gemini_image_active to True, and register it ahead of Pollinations in the image
provider chain (services/tools registry) — no other file changes needed, per the portability
contract in Architecture.md section 4.
"""
from __future__ import annotations

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from .base import ImageGenProvider, ImageResult


class GeminiImageProvider(ImageGenProvider):
    async def generate(self, *, prompt: str, aspect_ratio: str = "1:1") -> ImageResult:
        if not settings.gemini_image_active:
            raise ProviderUnavailable(
                "gemini_image",
                "inactive by config (free-tier quota exhausted) — see module docstring to reactivate",
            )
        raise NotImplementedError("Port image_providers/gemini_provider.py here once reactivated")
