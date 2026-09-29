"""Pure function, no I/O — classifies a crawled URL as "brand" or "product" before dispatching to
`BrandDnaService.crawl_brand_from_url` or `ProductDnaService.crawl_product_from_url`."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_PRODUCT_PATH_SEGMENTS = ("product", "products", "p", "item", "items", "dp", "sku")
_BRAND_PATH_SEGMENTS = ("about", "about-us", "company", "brand", "our-story")
_SKU_SEGMENT_RE = re.compile(r"^[a-z0-9]*\d{3,}[a-z0-9-]*$", re.IGNORECASE)


def detect_url_type(url: str) -> str:
    """"brand" | "product". Defaults to "brand" when nothing matches — a misclassified brand URL
    only touches the session's one brand row (easily corrected); a misclassified product URL
    spuriously creates a new ProductProfileModel row, so it's the one worth avoiding by default."""
    parsed = urlparse(url)
    segments = [s.lower() for s in parsed.path.split("/") if s]
    query = parse_qs(parsed.query)

    if any(k in query for k in ("id", "pid", "sku", "product_id")):
        return "product"
    if any(seg in _PRODUCT_PATH_SEGMENTS for seg in segments):
        return "product"
    if any(seg in _BRAND_PATH_SEGMENTS for seg in segments):
        return "brand"
    if any(_SKU_SEGMENT_RE.match(seg) for seg in segments):
        return "product"
    return "brand"
