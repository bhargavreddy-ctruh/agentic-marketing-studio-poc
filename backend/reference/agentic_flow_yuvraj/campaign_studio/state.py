"""Shared types for Campaign Studio events and run state."""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from ..agent_core.events import AgentEvent

# Alias kept for campaign_studio consumers
CampaignEvent = AgentEvent


class CampaignState(TypedDict, total=False):
    """Legacy shape retained for documentation; runtime uses CampaignRunContext."""

    idea: str
    product_image_urls: list[str]
    product_images_b64: list[dict[str, str]]
    brand_kit: dict[str, Any] | None
    asset_count: int
    aspect_ratio: str
    concept: dict[str, Any]
    storyboard: list[dict[str, Any]]
    copy: dict[str, Any]
    assets: list[dict[str, Any]]
    status: Literal["running", "completed", "failed"]
    error: str | None
