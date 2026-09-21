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
