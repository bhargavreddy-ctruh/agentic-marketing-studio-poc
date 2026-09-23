"""
video_providers -- Unified video generation provider chain.

Supports Gemini (Veo) and AWS (Nova Reel) with configurable fallback.
"""
from .types import VideoGenRequest, VideoSubmitResult, VideoItem, VideoPollResult
from .chain import submit_video_with_chain, poll_video_with_chain, PROVIDER_NAMES

__all__ = [
    "VideoGenRequest",
    "VideoSubmitResult",
    "VideoItem",
    "VideoPollResult",
    "submit_video_with_chain",
    "poll_video_with_chain",
    "PROVIDER_NAMES",
]
