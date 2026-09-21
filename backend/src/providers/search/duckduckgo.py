"""
THE ONLY file that imports the `ddgs` library — DuckDuckGo, the active `web_trend_search`
provider (Architecture.md section 1b). Genuinely free, no API key, no signup: real, live-verified
results for a real query before this was wired into the tool.

`ddgs`'s `DDGS().text()` call is synchronous (it's a thin wrapper over a blocking HTTP request),
so it's run via `asyncio.to_thread` here rather than blocking the event loop — every other
provider in this codebase talks over `httpx`'s async client directly; this is the one exception,
scoped to this file only, because the library itself gives no async option.

Swapping to a different search provider later (Tavily, Brave, etc. — real per-key rate limits or
better result quality) is a one-line change in `services/tools/web_trend_search.py`'s import,
exactly the same pattern already used for image/video providers (Rules.md section 5) — implement
`SearchProvider` in a new file here, then point the tool at it.
"""
from __future__ import annotations

import asyncio

from ddgs import DDGS
from ddgs.exceptions import DDGSException

from ...core.exceptions import ProviderUnavailable
from .base import SearchProvider, SearchResult


class DuckDuckGoSearchProvider(SearchProvider):
    async def search(self, *, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            raw = await asyncio.to_thread(
                lambda: list(DDGS().text(query, max_results=max_results))
            )
        except DDGSException as exc:
            raise ProviderUnavailable("duckduckgo", str(exc)) from exc

        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("href", ""),
                snippet=item.get("body", ""),
            )
            for item in raw
        ]


_singleton: DuckDuckGoSearchProvider | None = None


def get_search_provider() -> SearchProvider:
    global _singleton
    if _singleton is None:
        _singleton = DuckDuckGoSearchProvider()
    return _singleton
