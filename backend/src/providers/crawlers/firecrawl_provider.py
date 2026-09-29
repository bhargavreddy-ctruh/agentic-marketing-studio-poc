"""
THE ONLY file that imports firecrawl-py. Fallback for playwright_scraper.py when a real Playwright
render fails or times out — same "try primary, fall back on failure" shape as every other provider
chain in this app (Groq->Replicate, HuggingFace->Cloudflare). Returns the same `ScrapedPage` shape;
`image_urls`/`dominant_colors` may come back empty since Firecrawl's scrape API returns
markdown/HTML, not a rendered page ColorThief can sample without a second fetch — an acceptable,
disclosed degradation for a fallback path.

Constructed fresh on every call (never a cached singleton) since crawls are infrequent, unlike the
LLM/image providers that get called constantly — a live settings_service.py override of
firecrawl_api_key takes effect on the very next call with nothing to invalidate.
"""
from __future__ import annotations

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from .types import ScrapedPage


async def scrape_url_via_firecrawl(url: str) -> ScrapedPage:
    if not settings.firecrawl_api_key:
        raise ProviderUnavailable("firecrawl", "firecrawl_api_key is not set")

    try:
        from firecrawl import FirecrawlApp
    except ImportError as exc:  # pragma: no cover - dependency always installed in the image
        raise ProviderUnavailable("firecrawl", f"firecrawl-py not installed: {exc}") from exc

    try:
        app = FirecrawlApp(api_key=settings.firecrawl_api_key)
        result = await app.scrape_url_async(url, formats=["markdown", "html"])
    except Exception as exc:
        raise ProviderUnavailable("firecrawl", f"failed to scrape {url}: {exc}") from exc

    html = getattr(result, "html", None) or ""
    markdown = getattr(result, "markdown", None) or ""
    metadata = getattr(result, "metadata", None) or {}
    title = metadata.get("title", "") if isinstance(metadata, dict) else ""

    return ScrapedPage(
        url=url, html=html, title=title, text_content=markdown[:6000],
        image_urls=[], dominant_colors=[],
    )
