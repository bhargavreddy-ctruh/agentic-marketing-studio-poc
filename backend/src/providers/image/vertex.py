"""
THE ONLY file that would talk to Vertex AI's image model — registered but INACTIVE by default.

Stub only — see providers/image/gemini.py's docstring for why, and how to reactivate by porting
the existing agentic_flow codebase's image_providers/vertex_provider.py.
"""
from __future__ import annotations

from ...core.exceptions import ProviderUnavailable
from .base import ImageGenProvider, ImageResult


class VertexImageProvider(ImageGenProvider):
    async def generate(self, *, prompt: str, aspect_ratio: str = "1:1") -> ImageResult:
        raise ProviderUnavailable("vertex_image", "inactive — not needed while Pollinations is primary")
