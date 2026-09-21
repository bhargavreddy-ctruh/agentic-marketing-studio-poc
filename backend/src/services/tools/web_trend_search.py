"""
Web/Trend Search tool — Architecture.md section 1b. Wraps whichever SearchProvider is currently
active (DuckDuckGo today — real, free, no key needed, per `providers/search/duckduckgo.py`'s own
docstring on how to swap it for another provider later).
"""
from __future__ import annotations

from ...core.exceptions import ProviderUnavailable
from ...providers.search.duckduckgo import get_search_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("web_trend_search")
class WebTrendSearchTool(Tool):
    name = "web_trend_search"
    description = "Searches the web for current visual trend references matching a style brief."
    input_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    async def run(self, args: dict) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, data={}, error="query is required")

        provider = get_search_provider()
        try:
            results = await provider.search(query=query, max_results=5)
        except ProviderUnavailable as exc:
            # Same honest-degradation shape the stub always returned — a search hiccup lets
            # Reference Curator proceed on the brief alone, never a hard tool-call failure.
            return ToolResult(ok=True, data={"results": [], "configured": True, "error": exc.message})

        return ToolResult(
            ok=True,
            data={
                "results": [
                    {"title": r.title, "url": r.url, "snippet": r.snippet} for r in results
                ],
                "configured": True,
            },
        )
