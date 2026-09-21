"""The AudioGenProvider contract every text-to-speech provider must satisfy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class AudioResult:
    """Our own result type — never the vendor's raw response object."""

    audio_bytes: bytes
    mime_type: str
    provider_name: str


class AudioGenProvider(Protocol):
    async def synthesize(self, *, text: str, voice: str | None = None) -> AudioResult:
        """
        `voice` is a real, requested parameter when supplied, but whether a given provider can
        honor a specific named voice depends on that provider's real API — check the ACTIVE
        provider's own file for what actually happens with it, same disclosure rule as
        `VideoGenProvider.generate()`'s aspect_ratio/resolution parameters.
        """
        ...
