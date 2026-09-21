"""
Web/Trend Search tool — Architecture.md section 1b.

Honest Phase 1 scope: no web search API key/provider is configured for this POC yet. Rather than
faking search results, this returns a clearly-marked "not configured" response so Reference
Curator can proceed on the brief alone instead of silently hallucinating references. Wiring a real
search API (if one becomes available) is a config change to this one file — Rules.md section 5.
"""
from __future__ import annotations

from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("web_trend_search")
class WebTrendSearchTool(Tool):
    name = "web_trend_search"
    description = "Searches the web for current visual trend references matching a style brief."
    input_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    async def run(self, args: dict) -> ToolResult:
        return ToolResult(ok=True, data={"results": [], "configured": False})
