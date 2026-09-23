"""
types.py -- Common types for the unified video provider chain.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VideoGenRequest:
    """
    Unified request for video generation across all providers.

    Fields:
        prompt: Text describing the desired video motion and scene.
        image_base64: Base64-encoded start frame (before). Required for before/after.
        image_mime_type: MIME type of image_base64.
        end_image_base64: Base64-encoded end frame (after). Required for before/after.
        end_image_mime_type: MIME type of end_image_base64.
        aspect_ratio: Desired output aspect ratio ("16:9", "9:16", "1:1").
        sample_count: Number of video samples to generate (1 or 2).
        duration_seconds: Video duration in seconds (3, 5, or 10).
        resolution: Video resolution ("720p", "1080p", "4k").
        brand_memory: Optional brand context appended to the prompt.
    """
    prompt: str
    image_base64: str | None = None
    image_mime_type: str = "image/jpeg"
    end_image_base64: str | None = None
    end_image_mime_type: str = "image/jpeg"
    aspect_ratio: str = "16:9"
    sample_count: int = 1
    duration_seconds: int = 5
    resolution: str = "1080p"
    brand_memory: str | None = None


@dataclass
class VideoSubmitResult:
    """Result of a successful video job submission."""
    operation_id: str
    provider_used: str
    model_used: str
    api_key_for_poll: str | None = None  # Gemini: must use same key for poll as submit


@dataclass
class VideoItem:
    """A single generated video result."""
    data_url: str
    mime_type: str


@dataclass
class VideoPollResult:
    """
    Result of a single poll against a video long-running operation.

    Fields:
        done: True when the operation has completed (success or error).
        videos: List of VideoItem results. Present only when done=True and succeeded.
        data_url: Convenience: data_url of the first video (videos[0]).
        mime_type: MIME type of the first video.
        error: Error message string when done=True and operation failed.
    """
    done: bool
    videos: list[VideoItem] | None = None
    data_url: str | None = None
    mime_type: str | None = None
    error: str | None = None
