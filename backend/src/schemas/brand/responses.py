"""The public contract for a Brand DNA profile."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BrandProfileResponse(BaseModel):
    id: str
    name: str
    raw_facts: dict
    guardrails: dict
    indexed: bool
    created_at: datetime
    # Real, live-found gap (2026-09-28, explicit user report: "brand dna scraper is not scraping
    # the brand logo... it has to show in these fields") — these were already real, persisted
    # BrandProfileModel columns (set by the manual logo/font upload routes) but never surfaced
    # through this response, so the frontend had no way to know a logo/font already existed.
    logo_storage_ref: str | None = None
    font_storage_refs: dict = {}
    # Same direct-CDN-url latency win as CanvasElementResponse.url (2026-09-29) — None in
    # local-disk dev mode or when no logo has been uploaded/crawled yet.
    logo_url: str | None = None
