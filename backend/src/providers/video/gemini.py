"""
THE ONLY file that would talk to Veo via Gemini — registered but INACTIVE by default.

Stub only — see providers/image/gemini.py's docstring for the reasoning. When quota is available
again, port the existing agentic_flow codebase's video_providers/gemini_video.py (including its
proven poll/resubmit-on-failure logic) here, flip settings.gemini_video_active to True.
"""
from __future__ import annotations

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from .base import VideoGenProvider, VideoResult


class GeminiVideoProvider(VideoGenProvider):
    async def generate(
        self, *, prompt: str, image_bytes: bytes | None = None, duration_seconds: int = 5, **kwargs
    ) -> VideoResult:
        if not settings.gemini_video_active:
            raise ProviderUnavailable(
                "gemini_video",
                "inactive by config (free-tier quota exhausted) — see module docstring to reactivate",
            )
        raise NotImplementedError("Port video_providers/gemini_video.py here once reactivated")
