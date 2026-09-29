"""
THE ONLY file that imports playwright/bs4/colorthief. Renders a URL headlessly, extracts visible
text/title/image URLs via BeautifulSoup, and samples the hero image's dominant colors via
ColorThief. Returns the shared `ScrapedPage` type — never a raw Playwright/BeautifulSoup object —
so callers stay ignorant of how the page was actually fetched.

Primary path for services/knowledge/{brand,product}_dna_service.py's crawl_*_from_url methods;
firecrawl_provider.py is the fallback when this fails or times out.
"""
from __future__ import annotations

import asyncio
import json
import re
from io import BytesIO
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from colorthief import ColorThief

from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .types import ScrapedPage

log = get_logger(__name__)

_FONT_FACE_RE = re.compile(r"@font-face\s*\{([^}]*)\}", re.IGNORECASE | re.DOTALL)
_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*['\"]?([^;'\"}]+)['\"]?", re.IGNORECASE)
_FONT_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")
_ICON_FONT_KEYWORDS = ("icon", "fontawesome", "font-awesome", "glyphicon", "material-icons", "material-symbols")
_MAX_STYLESHEETS_TO_FETCH = 4

_MAX_TEXT_CHARS = 6000  # enough for LLM extraction context, without flooding the prompt
_MAX_IMAGE_CANDIDATES = 15

# Real, live-found gap (2026-09-28): the original DOM <img>-scan-in-order heuristic only worked by
# luck on the one site it was tested against (Flipkart) — a DIFFERENT site's DOM order, lazy-load
# markup, or icon-sprite conventions could just as easily put logos/ads/tracking-pixels first. Two
# near-universal, structured signals exist across most real e-commerce/CMS sites regardless of
# layout, and are tried FIRST, before ever falling back to the DOM scan:
#   1. schema.org Product JSON-LD (`<script type="application/ld+json">` with "@type": "Product") —
#      a real SEO/rich-snippet standard; its "image" field is the site's OWN declaration of which
#      images actually represent the product, not a guess from DOM position.
#   2. Open Graph / Twitter Card meta tags (`og:image`, `twitter:image`) — the single canonical
#      "this is what represents this page when shared" image, near-universally present.
_LOGO_ICON_KEYWORDS = (
    "logo", "favicon", "sprite", "icon-", "-icon", "badge", "rating", "star-",
    "payment", "flag-", "pixel", "tracking", "placeholder",
)


def _extract_jsonld_product_images(soup: BeautifulSoup, base_url: str) -> list[str]:
    urls: list[str] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            entry_type = entry.get("@type")
            types = entry_type if isinstance(entry_type, list) else [entry_type]
            if "Product" not in types:
                continue
            image = entry.get("image")
            if isinstance(image, str):
                urls.append(urljoin(base_url, image))
            elif isinstance(image, list):
                urls.extend(urljoin(base_url, i) for i in image if isinstance(i, str))
            elif isinstance(image, dict) and isinstance(image.get("url"), str):
                urls.append(urljoin(base_url, image["url"]))
    return urls


def _extract_meta_images(soup: BeautifulSoup, base_url: str) -> list[str]:
    urls: list[str] = []
    for prop in ("og:image", "og:image:secure_url", "twitter:image"):
        for tag in soup.find_all("meta", attrs={"property": prop}) + soup.find_all("meta", attrs={"name": prop}):
            content = tag.get("content")
            if content:
                urls.append(urljoin(base_url, content))
    return urls


def _is_logo_or_icon(absolute_url: str) -> bool:
    lower = absolute_url.lower()
    return lower.endswith(".svg") or any(kw in lower for kw in _LOGO_ICON_KEYWORDS)


async def _extract_logo_url(soup: BeautifulSoup, base_url: str) -> str | None:
    """The brand crawler's counterpart to the product-image extraction above — deliberately the
    OPPOSITE priority: a "logo" keyword match, which `_is_logo_or_icon` treats as noise to skip
    for a PRODUCT photo, is exactly the positive signal wanted here."""
    # 1. schema.org Organization JSON-LD — same structured-data approach as Product images, and
    # just as near-universal for a real business's own site.
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            entry_type = entry.get("@type")
            types = entry_type if isinstance(entry_type, list) else [entry_type]
            if "Organization" not in types and "Brand" not in types:
                continue
            logo = entry.get("logo")
            if isinstance(logo, str):
                return urljoin(base_url, logo)
            if isinstance(logo, dict) and isinstance(logo.get("url"), str):
                return urljoin(base_url, logo["url"])

    # 2. An <img> whose alt/class/id actually says "logo" — typically in the page header.
    for img in soup.find_all("img", limit=200):
        haystack = " ".join(
            str(img.get(attr, "")) for attr in ("alt", "class", "id", "src")
        ).lower()
        if "logo" not in haystack:
            continue
        src = img.get("src") or img.get("data-src")
        if src:
            return urljoin(base_url, src)

    # 3. A real, if lower-quality, fallback: the site's own declared touch-icon/favicon.
    for rel in ("apple-touch-icon", "icon", "shortcut icon"):
        link = soup.find("link", rel=rel)
        if link and link.get("href"):
            return urljoin(base_url, link["href"])

    # 4. Real, live-found gap (2026-09-29, via a live crawl that found nothing at all for a site
    # with none of the 3 signals above): plenty of sites never declare a <link rel="icon"> tag at
    # all, relying purely on the browser's implicit `/favicon.ico` convention. That's still a real,
    # if lowest-quality, brand mark — worth a quick existence check (HEAD, short timeout) rather
    # than returning it unverified, since a 404 here would otherwise surface as a silent
    # `crawler_logo_download_failed` warning downstream for no reason.
    favicon_url = urljoin(base_url, "/favicon.ico")
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            resp = await client.head(favicon_url)
            if resp.status_code == 200:
                return favicon_url
    except Exception as exc:
        log.warning("crawler_favicon_probe_failed", extra={"_extra_url": favicon_url, "_extra_error": str(exc)})

    return None


def _parse_font_faces(css_text: str, base_url: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for block in _FONT_FACE_RE.findall(css_text):
        family_match = _FONT_FAMILY_RE.search(block)
        url_match = _FONT_URL_RE.search(block)
        if not family_match or not url_match:
            continue
        family = family_match.group(1).strip()
        if any(kw in family.lower() for kw in _ICON_FONT_KEYWORDS):
            continue
        font_url = urljoin(base_url, url_match.group(1).strip())
        if font_url.lower().endswith(".svg"):  # legacy SVG-font fallback, not a real font file
            continue
        found.append((family, font_url))
    return found


async def _extract_fonts(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    """Real @font-face rules — inline <style> blocks first (already resolved, no extra fetch),
    then a handful of linked stylesheets (including Google Fonts' own generated CSS, which embeds
    the actual @font-face rules server-side). A modern browser User-Agent matters here: Google
    Fonts serves legacy .eot/.ttf to an unrecognized client and .woff2 to a real browser."""
    fonts: list[tuple[str, str]] = []
    seen_families: set[str] = set()

    def _merge(pairs: list[tuple[str, str]]) -> None:
        for family, font_url in pairs:
            if family.lower() not in seen_families:
                seen_families.add(family.lower())
                fonts.append((family, font_url))

    for style_tag in soup.find_all("style"):
        _merge(_parse_font_faces(style_tag.get_text() or "", base_url))

    stylesheet_urls = [
        urljoin(base_url, link["href"])
        for link in soup.find_all("link", rel="stylesheet")
        if link.get("href")
    ][:_MAX_STYLESHEETS_TO_FETCH]

    if stylesheet_urls:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=headers) as client:
            for sheet_url in stylesheet_urls:
                try:
                    resp = await client.get(sheet_url)
                    resp.raise_for_status()
                    _merge(_parse_font_faces(resp.text, sheet_url))
                except Exception as exc:
                    log.warning("crawler_stylesheet_fetch_failed", extra={"_extra_url": sheet_url, "_extra_error": str(exc)})
                    continue  # one unreachable stylesheet never fails the whole crawl

    return fonts


async def scrape_url(url: str, *, timeout_s: float = 20.0) -> ScrapedPage:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - dependency always installed in the image
        raise ProviderUnavailable("playwright", f"playwright not installed: {exc}") from exc

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=timeout_s * 1000)
                html = await page.content()
                title = await page.title()
            finally:
                await browser.close()
    except Exception as exc:
        raise ProviderUnavailable("playwright", f"failed to render {url}: {exc}") from exc

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text_content = " ".join(soup.get_text(separator=" ").split())[:_MAX_TEXT_CHARS]

    image_urls: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        parsed = urlparse(candidate)
        if parsed.scheme not in ("http", "https") or candidate in seen or _is_logo_or_icon(candidate):
            return
        seen.add(candidate)
        image_urls.append(candidate)

    # Priority 1 & 2: structured, site-declared signals — work regardless of any one site's DOM
    # layout/lazy-load conventions.
    for candidate in _extract_jsonld_product_images(soup, url):
        _add(candidate)
    for candidate in _extract_meta_images(soup, url):
        _add(candidate)

    # Priority 3 (fallback, and to fill out remaining slots up to the cap): the raw DOM <img> scan.
    # Real, live-found bug (2026-09-28): grabbing the first N <img> tags in DOM order used to pick
    # up header logos/nav icons BEFORE any real product photo on at least one real site — the
    # resulting canvas tiles were site-chrome, not the product. `_is_logo_or_icon` (via `_add`)
    # filters those; a tiny explicit width/height (common for nav icons/badges) is a further signal.
    for img in soup.find_all("img", limit=_MAX_IMAGE_CANDIDATES * 6):
        if len(image_urls) >= _MAX_IMAGE_CANDIDATES:
            break
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        width = img.get("width")
        height = img.get("height")
        try:
            if width and height and int(width) < 64 and int(height) < 64:
                continue
        except ValueError:
            pass
        _add(urljoin(url, src))

    image_urls = image_urls[:_MAX_IMAGE_CANDIDATES]
    logo_url = await _extract_logo_url(soup, url)
    fonts = await _extract_fonts(soup, url)

    dominant_colors = await _dominant_colors_for(image_urls[0]) if image_urls else []

    return ScrapedPage(
        url=url, html=html, title=title, text_content=text_content,
        image_urls=image_urls, dominant_colors=dominant_colors, logo_url=logo_url, fonts=fonts,
    )


async def _dominant_colors_for(image_url: str) -> list[str]:
    """Best-effort — a hero image that fails to download or isn't a real image just means an
    empty palette, never a failed crawl."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            image_bytes = resp.content
        return await asyncio.to_thread(_extract_palette, image_bytes)
    except Exception:
        return []


def _extract_palette(image_bytes: bytes) -> list[str]:
    thief = ColorThief(BytesIO(image_bytes))
    palette = thief.get_palette(color_count=5)
    return [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in palette]
