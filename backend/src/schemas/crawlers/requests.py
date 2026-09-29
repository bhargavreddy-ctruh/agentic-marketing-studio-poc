"""The public contract for kicking off a Product/Brand crawl."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CrawlRequest(BaseModel):
    url: str = Field(..., min_length=1)
