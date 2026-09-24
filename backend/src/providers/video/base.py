"""The VideoGenProvider contract every video provider must satisfy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class VideoResult:
    """Our own result type — never the vendor's raw response object."""

    video_bytes: bytes
    mime_type: str
    provider_name: str
    duration_seconds: int


class VideoGenProvider(Protocol):
    async def generate(
        self,
        *,
        prompt: str,
        image_bytes: bytes | None = None,
        duration_seconds: int = 5,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        camera_motion: str | None = None,
        first_frame_bytes: bytes | None = None,
        last_frame_bytes: bytes | None = None,
    ) -> VideoResult:
        """
        aspect_ratio and resolution are real, requested parameters — never decoration — but
        whether a given provider can actually honor either one depends on that provider's real
        API. A provider MUST do one of: (a) genuinely apply the value, (b) apply it indirectly and
        say how (e.g. by shaping an upstream input) in its own module docstring, or (c) ignore it
        and say so explicitly in its own module docstring and via a log line — silently dropping a
        requested value with no trace anywhere is the one thing never allowed. Check the ACTIVE
        provider's own file (not this protocol) for what actually happens with these two
        parameters today; do not assume this docstring alone guarantees behavior.
        """
        ...
