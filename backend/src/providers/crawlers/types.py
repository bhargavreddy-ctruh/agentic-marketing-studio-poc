"""Shared return shape for both crawler providers (playwright_scraper.py and firecrawl_provider.py)
so callers (the DNA services' crawl_*_from_url methods) never need to know which one ran."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScrapedPage:
    url: str
    html: str
    title: str
    text_content: str
    image_urls: list[str] = field(default_factory=list)
    dominant_colors: list[str] = field(default_factory=list)
    # The site's own brand mark (Organization JSON-LD "logo", an <img alt/class/id="logo">, or a
    # touch-icon/favicon as a last resort) — distinct from image_urls (product photos): a brand
    # crawl wants THIS field, a product crawl wants image_urls, never the other's list.
    logo_url: str | None = None
    # (family_name, font_file_url) pairs from real @font-face rules (inline <style> or a linked
    # stylesheet, including Google Fonts' own generated CSS) — the site's own actual typeface,
    # not a guess. Icon fonts (Font Awesome, Material Icons, etc.) are filtered out by name.
    fonts: list[tuple[str, str]] = field(default_factory=list)
