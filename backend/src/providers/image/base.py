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
        reference_image_bytes: bytes | None = None,
        reference_mime_type: str | None = None,
        style_reference_bytes: bytes | None = None,
        style_reference_mime_type: str | None = None,
        width: int | None = None,
        height: int | None = None,
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
        mask_bytes: bytes | None = None,
        mask_mime_type: str | None = None,
    ) -> ImageResult: ...
