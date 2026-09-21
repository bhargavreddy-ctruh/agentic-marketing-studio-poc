"""The SearchProvider contract every web-search provider must satisfy — Architecture.md section
1b's `web_trend_search` tool. Mirrors `providers/image/base.py`'s shape: a Protocol plus our own
result type, never the vendor's raw response object."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SearchResult:
    """Our own result type — never the vendor's raw response shape."""

    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    async def search(self, *, query: str, max_results: int = 5) -> list[SearchResult]: ...
