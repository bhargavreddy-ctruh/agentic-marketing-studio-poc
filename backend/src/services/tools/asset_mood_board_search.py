"""
Asset/Mood Board Search tool — Architecture.md section 1b.

Honest Phase 1 scope: no internal DAM (digital asset manager) exists in this POC yet — there is no
library of prior-campaign assets to search. Returns a clearly-marked "not configured" response,
same reasoning as web_trend_search.py.
"""
from __future__ import annotations

from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("asset_mood_board_search")
class AssetMoodBoardSearchTool(Tool):
    name = "asset_mood_board_search"
    description = "Searches internal prior-campaign assets for mood-board references."
    input_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    async def run(self, args: dict) -> ToolResult:
        return ToolResult(ok=True, data={"results": [], "configured": False})
