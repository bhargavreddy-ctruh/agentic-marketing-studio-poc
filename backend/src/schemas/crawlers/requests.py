"""The public contract for kicking off a Product/Brand crawl."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CrawlRequest(BaseModel):
    url: str = Field(..., min_length=1)
    # Real, live-found bug (2026-10-05, fidelity audit): without this, the backend had to GUESS
    # brand vs product purely from the URL's own shape (`url_classifier.py`'s `detect_url_type`),
    # defaulting to "brand" whenever nothing matched — confirmed live: a real product URL
    # (apple.com/airpods-pro/) submitted from the Product DNA tab got silently classified and
    # saved as a BRAND crawl instead, overwriting the session's brand row while the Product DNA
    # tab stayed empty. The caller (the Brand DNA tab, the Product DNA tab, or the
    # workflow-creation dialog's own two separate URL fields) always KNOWS which one it means —
    # optional so the chat "Add a link" popover (no tab context) can still omit it and fall back
    # to the heuristic.
    url_type: Literal["brand", "product"] | None = None
