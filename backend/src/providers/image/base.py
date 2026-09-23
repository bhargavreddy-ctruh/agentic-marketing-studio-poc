"""The ImageGenProvider contract every image provider (generation and editing) must satisfy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ImageResult:
    """Our own result type — never the vendor's raw response object."""

    image_bytes: bytes
    mime_type: str
    provider_name: str


class ImageGenProvider(Protocol):
    async def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str = "1:1",
        negative_prompt: str | None = None,
        enable_prompt_expansion: bool = True,
        seed: int | None = None,
        # Real, live-found gap (2026-09-24, per an explicit user ask: "image generation model
        # accepts image reference for generation, why are we not using that and its asking for
        # image to text then text to image"): `readme.md` documents `image` as a real input for
        # BOTH editing AND image-to-image generation, but only the edit path ever got it — a
        # referenced element handed to a `dynamic`-route generation step (illustrator) had no way
        # to ground in the real image bytes, only a lossy text description of it
        # (`session_service.py`'s `_describe_uploaded_image`), forcing a real image→text→image
        # round-trip even when the real reference image was one step away the whole time.
        reference_image_bytes: bytes | None = None,
        reference_mime_type: str | None = None,
    ) -> ImageResult: ...


class ImageEditProvider(Protocol):
    async def edit(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        instruction: str,
        aspect_ratio: str | None = None,
        match_input_image: bool = True,
        negative_prompt: str | None = None,
        enable_prompt_expansion: bool = True,
        seed: int | None = None,
    ) -> ImageResult: ...
