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
        self, *, prompt: str, aspect_ratio: str = "1:1"
    ) -> ImageResult: ...


class ImageEditProvider(Protocol):
    async def edit(
        self, *, image_bytes: bytes, mime_type: str, instruction: str
    ) -> ImageResult: ...
