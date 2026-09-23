"""
types.py -- Common types for the unified image provider chain.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ImageInput:
    """A single source image for image-to-image or multi-image generation."""
    data_b64: str
    mime_type: str


@dataclass
class ImageResult:
    """Output from a successful image generation call."""
    image_base64: str
    mime_type: str
    data_url: str
    provider_used: str
    model_used: str | None = None


@dataclass
class ImageGenRequest:
    """
    Unified request for image generation across all providers.

    Fields:
        prompt: Text describing the desired image or transformation.
        source_images: Optional list of source images (order matters for try-on).
        aspect_ratio: Desired output aspect ratio (e.g. "1:1", "4:3").
        system_instruction: Optional system instruction for Gemini/Vertex.
        negative_text: Optional text for what NOT to include (Bedrock).
        metadata: Optional provider-specific params (e.g. garment_class for Nova try-on).
    """
    prompt: str
    source_images: list[ImageInput] | None = None
    aspect_ratio: str = "1:1"
    system_instruction: str | None = None
    negative_text: str | None = None
    metadata: dict | None = None
